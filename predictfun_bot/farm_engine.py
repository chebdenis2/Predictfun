from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import math
import random
import time

from .config import Config
from .market_parser import build_market_base
from .models import FarmOrder, Market
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
    ) -> None:
        self._config = config
        self._client = client
        self._state = state
        self._logger = logger
        self._orders = orders

    def run_once(self, markets: list[Market]) -> None:
        now_ts = int(time.time())
        open_orders = self._fetch_open_orders()
        token_balances = self._fetch_token_balances()
        self._sync_orders(open_orders, now_ts)
        farm_markets = self._select_markets(markets, now_ts)
        self._ensure_market_limit(farm_markets)
        placed = 0
        for market in farm_markets:
            placed += self._ensure_market_orders(
                market,
                open_orders,
                token_balances,
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
                placed += self._ensure_market_orders(
                    market,
                    open_orders,
                    token_balances,
                    now_ts,
                    self._config.farm_relax_min_spread,
                    self._config.farm_relax_max_spread,
                )

    def _fetch_open_orders(self) -> dict[str, dict]:
        try:
            orders, _ = self._client.list_orders(status="OPEN", first=100)
        except Exception as exc:  # noqa: BLE001
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

    def _fetch_token_balances(self) -> dict[str, int]:
        try:
            payload = self._client.list_positions()
        except Exception as exc:  # noqa: BLE001
            self._logger.log_error("list_positions", str(exc))
            return {}
        return _parse_token_balances(payload)

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
        now_ts: int,
        min_spread: float,
        max_spread: float,
    ) -> int:
        market = self._enrich_market(market)
        prices = _compute_bid_ask(
            market,
            min_spread,
            max_spread,
            self._config.farm_top_levels,
        )
        if prices is None:
            self._logger.log_reject(market.market_id, market.title, "farm_no_prices", {})
            return 0
        token_id = _pick_yes_token_id(market)
        if not token_id:
            self._logger.log_reject(market.market_id, market.title, "farm_no_token", {})
            return 0
        bid_price, ask_price = prices
        placed = 0
        for side, price in (("buy", bid_price), ("sell", ask_price)):
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
                required_wei = self._orders.estimate_quantity_wei(
                    price,
                    size_usd,
                    market.decimal_precision,
                )
                available_wei = token_balances.get(token_id, 0)
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
            if not self._has_budget(size_usd):
                self._logger.log_reject(
                    market.market_id,
                    market.title,
                    "farm_budget",
                    {"size_usd": size_usd},
                )
                return placed
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
                response = self._client.create_order(payload)
            except OrderServiceError as exc:
                self._logger.log_reject(
                    market.market_id,
                    market.title,
                    "farm_order_size",
                    {"error": str(exc)},
                )
                continue
            except Exception as exc:  # noqa: BLE001
                self._logger.log_error("farm_place", str(exc))
                return placed
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

    def _enrich_market(self, market: Market) -> Market:
        if market.outcomes and market.decimal_precision > 0:
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
