from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import math
import random
import time

from .config import Config
from .market_parser import build_market_base
from .models import FarmOrder, Market
from .auth import AuthManager
from .order_service import OrderService, OrderServiceError
from .predictfun_client import PredictFunClient
from .state import StateStore
from .trade_logger import TradeLogger


class FarmEngine:
    def __init__(
        self,
        config: Config,
        client: PredictFunClient,
        state: StateStore,
        logger: TradeLogger,
        orders: OrderService,
        auth: AuthManager,
    ) -> None:
        self._config = config
        self._client = client
        self._state = state
        self._logger = logger
        self._orders = orders
        self._auth = auth

    def run_once(self, markets: list[Market]) -> None:
        now_ts = int(time.time())
        open_orders = self._fetch_open_orders()
        positions, token_balances = self._fetch_positions()
        positions_by_market = _positions_by_market(positions)
        self._sync_orders(open_orders, now_ts)
        stop_markets = self._handle_stop_losses(
            markets, open_orders, positions_by_market, token_balances, now_ts
        )
        farm_markets = self._select_markets(markets, now_ts)
        self._ensure_market_limit(farm_markets)
        placed = 0
        for market in farm_markets:
            if market.market_id in stop_markets:
                continue
            placed += self._ensure_market_orders(
                market,
                open_orders,
                token_balances,
                positions_by_market,
                now_ts,
                self._config.farm_min_spread,
                self._config.farm_max_spread,
            )
        if placed == 0 and self._config.farm_auto_relax:
            self._logger.log_info(
                "farm_auto_relax",
                {
                    "min_spread": self._config.farm_relax_min_spread,
                    "max_spread": self._config.farm_relax_max_spread,
                },
            )
            for market in farm_markets:
                if market.market_id in stop_markets:
                    continue
                placed += self._ensure_market_orders(
                    market,
                    open_orders,
                    token_balances,
                    positions_by_market,
                    now_ts,
                    self._config.farm_relax_min_spread,
                    self._config.farm_relax_max_spread,
                )

    def _fetch_open_orders(self) -> dict[str, dict]:
        try:
            orders, _ = self._client.list_orders(status="OPEN", first=100)
        except Exception as exc:  # noqa: BLE001
            if _is_invalid_jwt(exc):
                try:
                    self._auth.ensure_jwt(force_refresh=True)
                    orders, _ = self._client.list_orders(status="OPEN", first=100)
                except Exception as refresh_exc:  # noqa: BLE001
                    self._logger.log_error("farm_auth_refresh", str(refresh_exc))
                    return {}
            else:
                self._logger.log_error("list_orders", str(exc))
                return {}
        mapping: dict[str, dict] = {}
        for item in orders:
            if not isinstance(item, dict):
                continue
            order = item.get("order") or {}
            order_hash = order.get("hash")
            if order_hash:
                mapping[str(order_hash)] = item
        return mapping

    def _fetch_positions(self) -> tuple[list[dict], dict[str, int]]:
        try:
            payload = self._client.list_positions()
        except Exception as exc:  # noqa: BLE001
            if _is_invalid_jwt(exc):
                try:
                    self._auth.ensure_jwt(force_refresh=True)
                    payload = self._client.list_positions()
                except Exception as refresh_exc:  # noqa: BLE001
                    self._logger.log_error("farm_auth_refresh", str(refresh_exc))
                    return [], {}
            else:
                self._logger.log_error("list_positions", str(exc))
                return [], {}
        positions = _parse_positions(payload)
        return positions, _parse_token_balances(payload)

    def _handle_stop_losses(
        self,
        markets: list[Market],
        open_orders: dict[str, dict],
        positions_by_market: dict[str, dict],
        token_balances: dict[str, int],
        now_ts: int,
    ) -> set[str]:
        if self._config.farm_stop_loss_pct <= 0:
            return set()
        markets_by_id = {market.market_id: market for market in markets}
        triggered: set[str] = set()
        for market_id, position in positions_by_market.items():
            entry_price = position.get("entry_price")
            if entry_price is None:
                continue
            market = markets_by_id.get(market_id)
            if not market or market.yes_bid is None:
                continue
            if market.yes_bid > entry_price * (1.0 - self._config.farm_stop_loss_pct):
                continue
            triggered.add(market_id)
            token_id = position.get("token_id") or _pick_yes_token_id(market)
            if not token_id:
                self._logger.log_reject(
                    market_id,
                    market.title,
                    "farm_stoploss_no_token",
                    {},
                )
                continue
            inventory_wei = int(position.get("amount_wei") or 0)
            if inventory_wei <= 0:
                continue
            existing = _find_state_order(self._state.get_farm_orders(), market_id, "sell")
            if existing and existing.order_id:
                try:
                    self._client.remove_orders([existing.order_id])
                    self._logger.log_info(
                        "farm_stoploss_cancel",
                        {"market_id": market_id, "order_id": existing.order_id},
                    )
                except Exception as exc:  # noqa: BLE001
                    self._logger.log_error("farm_cancel", str(exc))
                    continue
                self._state.remove_farm_order(market_id, "sell")
            price = _aggressive_sell_price(market)
            if price is None:
                self._logger.log_reject(
                    market_id,
                    market.title,
                    "farm_stoploss_no_price",
                    {},
                )
                continue
            size_usd = _inventory_usd(inventory_wei, price)
            if size_usd <= 0:
                continue
            expiry_minutes = max(1, self._config.farm_stop_loss_expiry_minutes)
            try:
                payload, _, _, order_hash = self._orders.build_limit_order(
                    side="sell",
                    token_id=token_id,
                    price=price,
                    size_usd=size_usd,
                    fee_rate_bps=market.fee_rate_bps,
                    decimal_precision=market.decimal_precision,
                    is_neg_risk=market.is_neg_risk,
                    is_yield_bearing=market.is_yield_bearing,
                    expiry_minutes=expiry_minutes,
                )
                response = self._create_order_with_jwt_retry(payload)
            except Exception as exc:  # noqa: BLE001
                self._logger.log_error("farm_stoploss", str(exc))
                continue
            order_id = _extract_order_id(response)
            farm_order = FarmOrder(
                market_id=market_id,
                side="sell",
                order_id=order_id,
                order_hash=order_hash,
                price=price,
                quantity_wei=inventory_wei,
                placed_at_ts=now_ts,
                status="OPEN",
            )
            self._state.set_farm_order(market_id, "sell", farm_order)
            self._logger.log_info(
                "farm_stoploss_placed",
                {
                    "market_id": market_id,
                    "price": price,
                    "entry_price": entry_price,
                    "size_usd": size_usd,
                    "expiry_minutes": expiry_minutes,
                },
            )
        return triggered

    def _sync_orders(self, open_orders: dict[str, dict], now_ts: int) -> None:
        for farm_order in self._state.list_farm_orders():
            open_order = open_orders.get(farm_order.order_hash)
            if not open_order:
                self._logger.log_info(
                    "farm_order_closed",
                    {
                        "market_id": farm_order.market_id,
                        "side": farm_order.side,
                        "order_hash": farm_order.order_hash,
                    },
                )
                self._state.remove_farm_order(farm_order.market_id, farm_order.side)
                continue
            order_id = open_order.get("id")
            amount = _safe_float(open_order.get("amount"))
            filled = _safe_float(open_order.get("amountFilled"))
            if order_id and not farm_order.order_id:
                updated = FarmOrder(
                    market_id=farm_order.market_id,
                    side=farm_order.side,
                    order_id=str(order_id),
                    order_hash=farm_order.order_hash,
                    price=farm_order.price,
                    quantity_wei=farm_order.quantity_wei,
                    placed_at_ts=farm_order.placed_at_ts,
                    status="OPEN",
                )
                self._state.set_farm_order(farm_order.market_id, farm_order.side, updated)
            if amount > 0 and 0 < filled < amount:
                self._logger.log_info(
                    "farm_partial_fill",
                    {
                        "market_id": farm_order.market_id,
                        "side": farm_order.side,
                        "filled": filled,
                        "amount": amount,
                    },
                )
            if now_ts - farm_order.placed_at_ts < self._config.farm_min_hold_sec:
                continue

    def _select_markets(self, markets: list[Market], now_ts: int) -> list[Market]:
        filtered: list[Market] = []
        target_volume = (self._config.farm_min_volume_usd + self._config.farm_max_volume_usd) / 2
        for market in markets:
            if market.volume_usd < self._config.farm_min_volume_usd:
                self._logger.log_reject(
                    market.market_id,
                    market.title,
                    "farm_low_volume",
                    {"volume_usd": market.volume_usd},
                )
                continue
            if market.volume_usd > self._config.farm_max_volume_usd:
                self._logger.log_reject(
                    market.market_id,
                    market.title,
                    "farm_high_volume",
                    {"volume_usd": market.volume_usd},
                )
                continue
            if len(market.yes_bids) < self._config.farm_top_levels or len(
                market.yes_asks
            ) < self._config.farm_top_levels:
                self._logger.log_reject(
                    market.market_id,
                    market.title,
                    "farm_shallow_book",
                    {"bids": len(market.yes_bids), "asks": len(market.yes_asks)},
                )
                continue
            mid = _mid_price(market)
            if mid is None:
                self._logger.log_reject(market.market_id, market.title, "farm_no_mid", {})
                continue
            self._state.append_market_history(
                market.market_id, now_ts, mid, self._config.farm_vol_samples
            )
            volatility = _calculate_volatility(self._state.get_market_history(market.market_id))
            if market.expiry_ts:
                minutes_to_expiry = (market.expiry_ts - now_ts) / 60
                if minutes_to_expiry < self._config.farm_avoid_minutes and volatility > self._config.farm_max_volatility:
                    self._logger.log_reject(
                        market.market_id,
                        market.title,
                        "farm_high_volatility",
                        {"volatility": volatility, "minutes_to_expiry": minutes_to_expiry},
                    )
                    continue
            filtered.append(market)
        filtered.sort(key=lambda m: abs(m.volume_usd - target_volume))
        return filtered

    def _ensure_market_limit(self, markets: list[Market]) -> None:
        max_markets = self._config.farm_max_open_markets
        if len(markets) <= max_markets:
            return
        del markets[max_markets:]

    def _ensure_market_orders(
        self,
        market: Market,
        open_orders: dict[str, dict],
        token_balances: dict[str, int],
        positions_by_market: dict[str, dict],
        now_ts: int,
        min_spread: float,
        max_spread: float,
    ) -> int:
        market = self._enrich_market(market)
        placed = 0
        for side in ("buy", "sell"):
            prices = _compute_bid_ask(
                market,
                min_spread,
                max_spread,
                self._config.farm_top_levels,
            )
            if prices is None:
                self._logger.log_reject(market.market_id, market.title, "farm_no_prices", {})
                return placed
            price = prices[0] if side == "buy" else prices[1]
            token_id = _pick_yes_token_id(market)
            if not token_id:
                token_id = positions_by_market.get(market.market_id, {}).get("token_id", "")
            if not token_id:
                self._logger.log_reject(market.market_id, market.title, "farm_no_token", {})
                return placed
            size_usd = self._random_order_usd()
            expiry_minutes = self._random_expiry_minutes()
            if size_usd <= 0:
                self._logger.log_reject(
                    market.market_id,
                    market.title,
                    "farm_min_size",
                    {
                        "min_usd": self._config.farm_order_usd_min,
                        "max_usd": self._config.farm_order_usd_max,
                    },
                )
                continue
            inventory_wei = positions_by_market.get(market.market_id, {}).get(
                "amount_wei", token_balances.get(token_id, 0)
            )
            if side == "buy":
                if inventory_wei > 0 and not self._config.farm_allow_position_add:
                    self._logger.log_reject(
                        market.market_id,
                        market.title,
                        "farm_position_open",
                        {"inventory_wei": inventory_wei},
                    )
                    continue
                if self._config.farm_allow_position_add and inventory_wei > 0:
                    inventory_usd = _inventory_usd(inventory_wei, price)
                    if inventory_usd >= self._config.farm_max_inventory_usd:
                        self._logger.log_reject(
                            market.market_id,
                            market.title,
                            "farm_inventory_limit",
                            {
                                "inventory_usd": inventory_usd,
                                "max_usd": self._config.farm_max_inventory_usd,
                            },
                        )
                        continue
                    if inventory_usd + size_usd > self._config.farm_max_inventory_usd:
                        self._logger.log_reject(
                            market.market_id,
                            market.title,
                            "farm_inventory_limit",
                            {
                                "inventory_usd": inventory_usd,
                                "max_usd": self._config.farm_max_inventory_usd,
                                "size_usd": size_usd,
                            },
                        )
                        continue
            existing = _find_state_order(self._state.get_farm_orders(), market.market_id, side)
            if existing:
                open_entry = open_orders.get(existing.order_hash)
                if open_entry and now_ts - existing.placed_at_ts < self._config.farm_min_hold_sec:
                    continue
                if open_entry and _order_in_top5(existing, market, self._config.farm_top_levels):
                    continue
                if existing.order_id:
                    try:
                        self._client.remove_orders([existing.order_id])
                        self._logger.log_info(
                            "farm_order_cancelled",
                            {"market_id": market.market_id, "side": side, "order_id": existing.order_id},
                        )
                    except Exception as exc:  # noqa: BLE001
                        self._logger.log_error("farm_cancel", str(exc))
                        continue
                self._state.remove_farm_order(market.market_id, side)
            if side == "sell":
                available_wei = inventory_wei
                inventory_usd = _inventory_usd(available_wei, price)
                if inventory_usd <= 0:
                    self._logger.log_reject(
                        market.market_id,
                        market.title,
                        "farm_no_inventory",
                        {"available_wei": available_wei},
                    )
                    continue
                size_usd = round(inventory_usd, 2)
                sell_quantity_wei = self._orders.estimate_quantity_wei(
                    price,
                    size_usd,
                    market.decimal_precision,
                )
                if sell_quantity_wei <= 0:
                    self._logger.log_reject(
                        market.market_id,
                        market.title,
                        "farm_dust",
                        {"available_wei": available_wei},
                    )
                    continue
                tail_wei = max(available_wei - sell_quantity_wei, 0)
                if tail_wei > 0:
                    self._logger.log_info(
                        "farm_sell_tail",
                        {"market_id": market.market_id, "tail_wei": tail_wei},
                    )
                required_wei = self._orders.estimate_quantity_wei(
                    price,
                    size_usd,
                    market.decimal_precision,
                )
                if required_wei <= 0:
                    self._logger.log_reject(
                        market.market_id,
                        market.title,
                        "farm_min_size",
                        {"price": price, "size_usd": self._config.farm_order_usd},
                    )
                    continue
                if available_wei < required_wei:
                    self._logger.log_reject(
                        market.market_id,
                        market.title,
                        "farm_no_inventory",
                        {
                            "available_wei": available_wei,
                            "required_wei": required_wei,
                            "size_usd": size_usd,
                        },
                    )
                    continue
            if side == "buy" and not self._has_budget(size_usd):
                self._logger.log_reject(
                    market.market_id,
                    market.title,
                    "farm_budget",
                    {"size_usd": size_usd},
                )
                return placed
            retried = False
            while True:
                try:
                    payload, quantity_wei, _, order_hash = self._orders.build_limit_order(
                        side=side,
                        token_id=token_id,
                        price=price,
                        size_usd=size_usd,
                        fee_rate_bps=market.fee_rate_bps,
                        decimal_precision=market.decimal_precision,
                        is_neg_risk=market.is_neg_risk,
                        is_yield_bearing=market.is_yield_bearing,
                        expiry_minutes=expiry_minutes,
                    )
                    response = self._create_order_with_jwt_retry(payload)
                    break
                except OrderServiceError as exc:
                    self._logger.log_reject(
                        market.market_id,
                        market.title,
                        "farm_order_size",
                        {"error": str(exc)},
                    )
                    response = None
                    break
                except Exception as exc:  # noqa: BLE001
                    if not retried and _is_hash_mismatch(exc):
                        retried = True
                        refreshed = self._enrich_market(market, force=True)
                        retry_prices = _compute_bid_ask(
                            refreshed,
                            min_spread,
                            max_spread,
                            self._config.farm_top_levels,
                        )
                        if not retry_prices:
                            self._logger.log_reject(
                                market.market_id, market.title, "farm_no_prices", {}
                            )
                            response = None
                            break
                        price = retry_prices[0] if side == "buy" else retry_prices[1]
                        token_id = _pick_yes_token_id(refreshed)
                        if not token_id:
                            self._logger.log_reject(
                                market.market_id, market.title, "farm_no_token", {}
                            )
                            response = None
                            break
                        if side == "sell":
                            required_wei = self._orders.estimate_quantity_wei(
                                price,
                                size_usd,
                                refreshed.decimal_precision,
                            )
                            available_wei = token_balances.get(token_id, 0)
                            if required_wei <= 0:
                                self._logger.log_reject(
                                    market.market_id,
                                    market.title,
                                    "farm_min_size",
                                    {
                                        "price": price,
                                        "size_usd": size_usd,
                                    },
                                )
                                response = None
                                break
                            if available_wei < required_wei:
                                self._logger.log_reject(
                                    market.market_id,
                                    market.title,
                                    "farm_no_inventory",
                                    {
                                        "available_wei": available_wei,
                                        "required_wei": required_wei,
                                        "size_usd": size_usd,
                                    },
                                )
                                response = None
                                break
                        market = refreshed
                        continue
                    self._logger.log_error("farm_place", str(exc))
                    return placed
            if response is None:
                continue
            order_id = _extract_order_id(response)
            farm_order = FarmOrder(
                market_id=market.market_id,
                side=side,
                order_id=order_id,
                order_hash=order_hash,
                price=price,
                quantity_wei=quantity_wei,
                placed_at_ts=now_ts,
                status="OPEN",
            )
            self._state.set_farm_order(market.market_id, side, farm_order)
            self._logger.log_info(
                "farm_order_placed",
                {
                    "market_id": market.market_id,
                    "side": side,
                    "price": price,
                    "order_id": order_id,
                    "hash": order_hash,
                    "size_usd": size_usd,
                    "expiry_minutes": expiry_minutes,
                },
            )
            placed += 1
        return placed

    def _has_budget(self, next_order_usd: float) -> bool:
        open_orders = self._state.list_farm_orders()
        _, max_usd = self._order_size_range()
        reserved = len(open_orders) * max_usd
        return reserved + next_order_usd <= self._config.budget_total_usd

    def _order_size_range(self) -> tuple[float, float]:
        min_usd = max(0.0, self._config.farm_order_usd_min)
        max_usd = max(min_usd, self._config.farm_order_usd_max)
        return min_usd, max_usd

    def _random_order_usd(self) -> float:
        min_usd, max_usd = self._order_size_range()
        if max_usd <= 0:
            return 0.0
        if min_usd == max_usd:
            return round(min_usd, 2)
        return round(random.uniform(min_usd, max_usd), 2)

    def _random_expiry_minutes(self) -> int:
        min_minutes = max(1, self._config.farm_order_expiry_min_minutes)
        max_minutes = max(min_minutes, self._config.farm_order_expiry_max_minutes)
        hold_minutes = max(1, math.ceil(self._config.farm_min_hold_sec / 60))
        if min_minutes < hold_minutes:
            min_minutes = hold_minutes
        if max_minutes < min_minutes:
            max_minutes = min_minutes
        if min_minutes == max_minutes:
            return min_minutes
        return random.randint(min_minutes, max_minutes)

    def _enrich_market(self, market: Market, force: bool = False) -> Market:
        if not force and market.outcomes and market.decimal_precision > 0:
            return market
        try:
            details = self._client.get_market_details(market.market_id)
        except Exception as exc:  # noqa: BLE001
            self._logger.log_error("farm_market_details", str(exc))
            return market
        enriched = build_market_base(details)
        return replace(
            market,
            fee_rate_bps=enriched.fee_rate_bps,
            is_neg_risk=enriched.is_neg_risk,
            is_yield_bearing=enriched.is_yield_bearing,
            decimal_precision=enriched.decimal_precision,
            outcomes=enriched.outcomes,
        )

    def _create_order_with_jwt_retry(self, payload: dict) -> object:
        try:
            return self._client.create_order(payload)
        except Exception as exc:  # noqa: BLE001
            if not _is_invalid_jwt(exc):
                raise
            try:
                self._auth.ensure_jwt(force_refresh=True)
            except Exception as refresh_exc:  # noqa: BLE001
                self._logger.log_error("farm_auth_refresh", str(refresh_exc))
                raise
            return self._client.create_order(payload)


def _mid_price(market: Market) -> float | None:
    if market.yes_bid is None or market.yes_ask is None:
        return None
    return (market.yes_bid + market.yes_ask) / 2


def _calculate_volatility(history: list[dict]) -> float:
    if len(history) < 3:
        return 0.0
    returns = []
    for idx in range(1, len(history)):
        prev = history[idx - 1]["mid"]
        curr = history[idx]["mid"]
        if prev <= 0:
            continue
        returns.append(abs(curr / prev - 1.0))
    if not returns:
        return 0.0
    return sum(returns) / len(returns)


def _compute_bid_ask(
    market: Market,
    min_spread: float,
    max_spread: float,
    top_levels: int,
) -> tuple[float, float] | None:
    if not market.yes_bids or not market.yes_asks:
        return None
    best_bid = market.yes_bids[0][0]
    best_ask = market.yes_asks[0][0]
    mid = (best_bid + best_ask) / 2
    target_spread = (min_spread + max_spread) / 2
    tick = 1 / (10**market.decimal_precision)
    bid_floor = market.yes_bids[min(len(market.yes_bids), top_levels) - 1][0]
    ask_ceiling = market.yes_asks[min(len(market.yes_asks), top_levels) - 1][0]
    bid_price = max(mid - target_spread / 2, bid_floor + tick)
    ask_price = min(mid + target_spread / 2, ask_ceiling - tick)
    bid_price = round(bid_price, market.decimal_precision)
    ask_price = round(ask_price, market.decimal_precision)
    if ask_price <= bid_price:
        return None
    spread = ask_price - bid_price
    if spread < min_spread:
        ask_price = round(bid_price + min_spread, market.decimal_precision)
    if spread > max_spread:
        ask_price = round(bid_price + max_spread, market.decimal_precision)
    if ask_price <= bid_price:
        return None
    if bid_price < bid_floor or ask_price > ask_ceiling:
        return None
    return bid_price, ask_price


def _order_in_top5(order: FarmOrder, market: Market, top_levels: int) -> bool:
    if order.side == "buy":
        bid_floor = market.yes_bids[min(len(market.yes_bids), top_levels) - 1][0]
        return order.price >= bid_floor
    ask_ceiling = market.yes_asks[min(len(market.yes_asks), top_levels) - 1][0]
    return order.price <= ask_ceiling


def _pick_yes_token_id(market: Market) -> str:
    targets = ("yes", "up")
    for outcome in market.outcomes:
        if outcome.name.lower() in targets:
            return outcome.token_id
    return ""


def _extract_order_id(response: object) -> str | None:
    if isinstance(response, dict):
        data = response.get("data")
        if isinstance(data, dict):
            order_id = data.get("id") or data.get("orderId")
            if order_id:
                return str(order_id)
    return None


def _find_state_order(state_orders: dict, market_id: str, side: str) -> FarmOrder | None:
    side_key = "buy" if side == "buy" else "sell"
    raw = state_orders.get(market_id, {}).get(side_key)
    if not raw:
        return None
    return FarmOrder(
        market_id=market_id,
        side=side_key,
        order_id=raw.get("order_id"),
        order_hash=raw.get("order_hash", ""),
        price=float(raw.get("price", 0.0)),
        quantity_wei=int(raw.get("quantity_wei", 0)),
        placed_at_ts=int(raw.get("placed_at_ts", 0)),
        status=str(raw.get("status", "")),
    )


def _safe_float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _inventory_usd(inventory_wei: int, price: float) -> float:
    if inventory_wei <= 0 or price <= 0:
        return 0.0
    shares = Decimal(inventory_wei) / Decimal("1e18")
    return float(shares * Decimal(str(price)))


def _is_hash_mismatch(exc: Exception) -> bool:
    message = str(exc)
    return "order hash mismatch" in message.lower()


def _is_invalid_jwt(exc: Exception) -> bool:
    message = str(exc).lower()
    return "invalid jwt" in message or "http 401" in message


def _aggressive_sell_price(market: Market) -> float | None:
    if market.yes_bid is None:
        return None
    tick = 1 / (10**market.decimal_precision)
    price = max(0.0, market.yes_bid - tick)
    return round(price, market.decimal_precision)


def _parse_positions(payload: object) -> list[dict]:
    data = payload
    if isinstance(payload, dict):
        data = payload.get("data")
    if not isinstance(data, list):
        return []
    positions: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        market_id = _extract_market_id(item)
        token_id = _extract_token_id(item)
        amount_wei = _extract_position_amount(item)
        if not market_id or not token_id or amount_wei <= 0:
            continue
        entry_price = _extract_entry_price(item, amount_wei)
        positions.append(
            {
                "market_id": market_id,
                "token_id": token_id,
                "amount_wei": amount_wei,
                "entry_price": entry_price,
            }
        )
    return positions


def _extract_market_id(item: dict) -> str:
    market_id = item.get("marketId") or item.get("market_id") or item.get("marketID")
    if not market_id:
        market = item.get("market")
        if isinstance(market, dict):
            market_id = market.get("id") or market.get("marketId")
    return str(market_id) if market_id else ""


def _extract_position_amount(item: dict) -> int:
    candidates = (
        "amount",
        "tokenAmount",
        "quantity",
        "shares",
        "positionSize",
        "amountAvailable",
        "available",
        "balance",
    )
    for key in candidates:
        if key in item and item[key] is not None:
            parsed = _parse_wei_amount(item[key])
            if parsed is not None:
                return parsed
    return 0


def _extract_entry_price(item: dict, amount_wei: int) -> float | None:
    candidates = (
        "avgPrice",
        "averagePrice",
        "avgEntryPrice",
        "entryPrice",
        "avgPricePerShare",
        "pricePerShare",
        "avgFillPrice",
    )
    for key in candidates:
        if key in item and item[key] is not None:
            return _normalize_price(item[key])
    cost_basis = None
    for key in ("costBasis", "totalCost", "costUsd", "notionalUsd"):
        if key in item and item[key] is not None:
            cost_basis = _parse_float(item[key])
            break
    if cost_basis is None or amount_wei <= 0:
        return None
    shares = float(Decimal(amount_wei) / Decimal("1e18"))
    if shares <= 0:
        return None
    return cost_basis / shares


def _normalize_price(value: object) -> float | None:
    raw = _parse_float(value)
    if raw is None:
        return None
    if raw > 1e9:
        return float(Decimal(str(raw)) / Decimal("1e18"))
    if raw > 1:
        return raw / 100.0
    return raw


def _parse_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _positions_by_market(positions: list[dict]) -> dict[str, dict]:
    mapping: dict[str, dict] = {}
    for position in positions:
        market_id = position.get("market_id")
        if not market_id:
            continue
        existing = mapping.get(market_id)
        if not existing or int(position.get("amount_wei", 0)) > int(existing.get("amount_wei", 0)):
            mapping[market_id] = position
    return mapping


def _parse_token_balances(payload: object) -> dict[str, int]:
    data = payload
    if isinstance(payload, dict):
        data = payload.get("data")
    if not isinstance(data, list):
        return {}
    balances: dict[str, int] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        token_id = _extract_token_id(item)
        if not token_id:
            continue
        amount = _extract_available_amount(item)
        if amount is None:
            continue
        current = balances.get(token_id, 0)
        if amount > current:
            balances[token_id] = amount
    return balances


def _extract_token_id(item: dict) -> str:
    token_id = item.get("tokenId") or item.get("token_id") or item.get("tokenID")
    if not token_id:
        token_id = item.get("outcomeTokenId") or item.get("outcome_token_id")
    if not token_id:
        token = item.get("token")
        if isinstance(token, dict):
            token_id = token.get("id") or token.get("tokenId")
    if not token_id:
        outcome = item.get("outcome")
        if isinstance(outcome, dict):
            token_id = outcome.get("tokenId") or outcome.get("onChainId") or outcome.get("id")
    return str(token_id) if token_id else ""


def _extract_available_amount(item: dict) -> int | None:
    candidates = [
        "amountAvailable",
        "available",
        "balance",
        "tokenAmount",
        "amount",
        "quantity",
        "positionSize",
        "shares",
    ]
    for key in candidates:
        if key in item and item[key] is not None:
            value = item[key]
            if isinstance(value, dict):
                for nested_key in ("available", "balance", "amount"):
                    if nested_key in value:
                        parsed = _parse_wei_amount(value[nested_key])
                        if parsed is not None:
                            return parsed
                continue
            parsed = _parse_wei_amount(value)
            if parsed is not None:
                return parsed
    return None


def _parse_wei_amount(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return max(value, 0)
    if isinstance(value, float):
        return max(int(Decimal(str(value)) * Decimal("1e18")), 0)
    if isinstance(value, str):
        token = value.strip()
        if not token:
            return None
        if token.isdigit():
            return int(token)
        try:
            return max(int(Decimal(token) * Decimal("1e18")), 0)
        except Exception:  # noqa: BLE001
            return None
    return None
