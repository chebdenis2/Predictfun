from __future__ import annotations

from datetime import datetime, timezone
import time

from . import binance
from .approvals import ApprovalManager
from .auth import AuthManager
from .config import Config
from .farm_engine import FarmEngine
from .market_parser import apply_orderbook, apply_stats, build_market_base
from .models import Market, Position
from .order_service import OrderService
from .pnl import append_daily_report
from .predictfun_client import PredictFunClient
from .state import StateStore
from .strategy import Strategy
from .trade_logger import TradeLogger


class Runner:
    def __init__(
        self,
        config: Config,
        predictfun_client: PredictFunClient,
        auth: AuthManager,
        approvals: ApprovalManager,
        state: StateStore,
        logger: TradeLogger,
        strategy: Strategy,
        order_service: OrderService,
    ) -> None:
        self._config = config
        self._predictfun = predictfun_client
        self._auth = auth
        self._approvals = approvals
        self._state = state
        self._logger = logger
        self._strategy = strategy
        self._orders = order_service
        self._farm = FarmEngine(config, predictfun_client, state, logger, order_service)

    def _available_budget(self) -> float:
        open_total = sum(position.size_usd for position in self._state.get_open_positions())
        return max(0.0, self._config.budget_total_usd - open_total)

    def _position_size(self, available_usd: float) -> float:
        target = self._config.budget_total_usd * self._config.position_size_max_pct
        minimum = self._config.budget_total_usd * self._config.position_size_min_pct
        size = min(target, available_usd)
        if size < minimum:
            return 0.0
        return size

    def _maybe_report_pnl(self) -> None:
        now = datetime.now(timezone.utc)
        date_str = now.date().isoformat()
        last_report = self._state.get_last_report_date()
        scheduled = now.replace(
            hour=self._config.pnl_report_hour_utc,
            minute=self._config.pnl_report_minute_utc,
            second=0,
            microsecond=0,
        )
        if last_report == date_str:
            return
        if now < scheduled:
            return
        report = append_daily_report(self._config.trade_log_path, self._config.pnl_log_path, date_str)
        print(f"Daily P&L report: {report}")
        self._state.set_last_report_date(date_str)

    def _log_info(self, context: str, payload: dict | None = None) -> None:
        if not self._config.verbose_logs:
            return
        self._logger.log_info(context, payload)
        if payload:
            print(f"[INFO] {context}: {payload}")
        else:
            print(f"[INFO] {context}")

    def _log_position_metrics(self, markets_by_id: dict[str, Market]) -> None:
        if not self._config.verbose_logs:
            return
        for position in self._state.get_open_positions():
            market = markets_by_id.get(position.market_id)
            if market is None:
                continue
            current_price = market.yes_bid if position.side == "YES" else market.no_bid
            if current_price is None or current_price <= 0:
                continue
            pnl_usd = position.size_usd * (current_price / position.entry_price - 1.0)
            self._logger.log_info(
                "position_pnl",
                {
                    "trade_id": position.trade_id,
                    "market_id": position.market_id,
                    "side": position.side,
                    "entry_price": position.entry_price,
                    "current_price": current_price,
                    "pnl_usd": pnl_usd,
                },
            )

    def _fetch_orderbook(self, market_id: str) -> dict | None:
        last_error = None
        retries = max(0, self._config.orderbook_retry_count)
        for attempt in range(retries + 1):
            try:
                return self._predictfun.get_orderbook(market_id)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt >= retries:
                    break
                time.sleep(self._config.orderbook_retry_sleep_sec)
        if last_error:
            self._logger.log_error("orderbook", f"{market_id}: {last_error}")
        return None

    def _load_markets(self) -> list[Market]:
        try:
            start = time.monotonic()
            if self._config.predictfun_markets_source.lower() == "graphql":
                cursor = self._state.get_markets_cursor(
                    "graphql", self._config.markets_cursor_ttl_sec
                )
                result = self._predictfun.get_all_markets_graphql(
                    self._config.predictfun_max_pages,
                    self._config.predictfun_markets_page_size,
                    self._config.predictfun_page_sleep_sec,
                    self._config.predictfun_graphql_is_resolved,
                    cursor,
                    True,
                )
                raw_markets, end_cursor = result
                if end_cursor:
                    self._state.set_markets_cursor("graphql", end_cursor)
            else:
                cursor = self._state.get_markets_cursor(
                    "rest", self._config.markets_cursor_ttl_sec
                )
                result = self._predictfun.get_all_markets(
                    self._config.predictfun_max_pages,
                    self._config.predictfun_markets_page_size,
                    self._config.predictfun_page_sleep_sec,
                    self._config.predictfun_max_page_errors,
                    self._config.predictfun_markets_statuses,
                    cursor,
                    True,
                )
                raw_markets, end_cursor = result
                if end_cursor:
                    self._state.set_markets_cursor("rest", end_cursor)
            markets_latency_ms = (time.monotonic() - start) * 1000
        except Exception as exc:  # noqa: BLE001
            self._logger.log_error("list_markets", str(exc))
            return []
        markets: list[Market] = []
        total = len(raw_markets)
        allowed = 0
        with_orderbook = 0
        rejected_not_allowed = 0
        spreads = []
        orderbook_latencies = []
        stats_latencies = []
        for raw in raw_markets:
            if not isinstance(raw, dict):
                continue
            base = build_market_base(raw)
            if not base.market_id:
                continue
            if not self._strategy.is_allowed_market(base):
                rejected_not_allowed += 1
                if self._config.log_rejections:
                    self._logger.log_reject(
                        base.market_id,
                        base.title,
                        "not_allowed",
                        {
                            "status": base.status,
                            "kind": base.kind,
                            "symbol": base.symbol,
                            "resolution": base.resolution_minutes,
                            "mode": self._config.strategy_mode,
                        },
                    )
                continue
            allowed += 1
            ob_start = time.monotonic()
            orderbook = self._fetch_orderbook(base.market_id)
            if not orderbook:
                continue
            orderbook_latencies.append((time.monotonic() - ob_start) * 1000)
            with_orderbook += 1
            try:
                st_start = time.monotonic()
                stats = self._predictfun.get_market_stats(base.market_id)
                stats_latencies.append((time.monotonic() - st_start) * 1000)
            except Exception as exc:  # noqa: BLE001
                self._logger.log_error("market_stats", f"{base.market_id}: {exc}")
                stats = None
            market = apply_orderbook(base, orderbook)
            market = apply_stats(market, stats)
            if market.yes_ask is not None and market.yes_bid is not None:
                spreads.append(max(0.0, market.yes_ask - market.yes_bid))
            markets.append(market)
        self._log_info(
            "markets_loaded",
            {
                "total": total,
                "allowed": allowed,
                "with_orderbook": with_orderbook,
                "rejected_not_allowed": rejected_not_allowed,
                "markets_latency_ms": round(markets_latency_ms, 2),
                "orderbook_ms_avg": round(sum(orderbook_latencies) / len(orderbook_latencies), 2)
                if orderbook_latencies
                else None,
                "stats_ms_avg": round(sum(stats_latencies) / len(stats_latencies), 2)
                if stats_latencies
                else None,
                "spread_avg": round(sum(spreads) / len(spreads), 4) if spreads else None,
                "spread_max": round(max(spreads), 4) if spreads else None,
            },
        )
        return markets

    def _handle_exits(self, markets_by_id: dict[str, Market]) -> None:
        for position in self._state.get_open_positions():
            market = markets_by_id.get(position.market_id)
            if market is None:
                continue
            should_exit, reason, current_price = self._strategy.should_exit(position, market)
            if not should_exit:
                continue
            if current_price is None:
                self._logger.log_error("exit_price", f"{position.market_id}: missing price")
                continue
            pnl_usd = position.size_usd * (current_price / position.entry_price - 1.0)
            self._logger.log_info(
                "position_exit_signal",
                {
                    "trade_id": position.trade_id,
                    "market_id": position.market_id,
                    "side": position.side,
                    "pnl_usd": pnl_usd,
                    "reason": reason,
                },
            )
            if self._config.dry_run:
                self._logger.log_close(
                    position.trade_id,
                    position.market_id,
                    position.symbol,
                    position.side,
                    position.token_id,
                    position.quantity_wei,
                    position.size_usd,
                    position.entry_price,
                    current_price,
                    pnl_usd,
                    reason,
                )
                self._state.close_position(position.trade_id)
                continue
            try:
                self._auth.ensure_jwt()
                self._orders.ensure_approvals()
                payload, _, _ = self._orders.build_exit_order(position, current_price)
                self._predictfun.create_order(payload)
                self._logger.log_close(
                    position.trade_id,
                    position.market_id,
                    position.symbol,
                    position.side,
                    position.token_id,
                    position.quantity_wei,
                    position.size_usd,
                    position.entry_price,
                    current_price,
                    pnl_usd,
                    reason,
                )
                self._state.close_position(position.trade_id)
            except Exception as exc:  # noqa: BLE001
                self._logger.log_error("exit_order", str(exc))

    def _handle_entries(self, markets: list[Market]) -> None:
        if len(self._state.get_open_positions()) >= self._config.max_open_positions:
            return
        momentum_probabilities: dict[str, float] = {}
        spot_prices: dict[str, float] = {}
        for symbol in self._config.allowed_symbols:
            try:
                start = time.monotonic()
                momentum_probabilities[symbol] = binance.estimate_up_probability(
                    symbol,
                    self._config.binance_base_url,
                    self._config.binance_timeout_sec,
                    self._config.model_lookback_minutes,
                    self._config.model_k,
                )
                self._log_info(
                    "binance_momentum_latency",
                    {"symbol": symbol, "ms": round((time.monotonic() - start) * 1000, 2)},
                )
            except Exception as exc:  # noqa: BLE001
                self._logger.log_error("binance_probability", str(exc))
            try:
                start = time.monotonic()
                spot_prices[symbol] = binance.get_spot_price(
                    symbol,
                    self._config.binance_base_url,
                    self._config.binance_timeout_sec,
                )
                self._log_info(
                    "binance_spot_latency",
                    {"symbol": symbol, "ms": round((time.monotonic() - start) * 1000, 2)},
                )
            except Exception as exc:  # noqa: BLE001
                self._logger.log_error("binance_spot", str(exc))
        candidates = []
        rejected = 0
        for market in markets:
            start = time.monotonic()
            candidate, reason, details = self._strategy.evaluate_market(
                market,
                momentum_probabilities,
                spot_prices,
            )
            self._log_info(
                "evaluate_market_latency",
                {"market_id": market.market_id, "ms": round((time.monotonic() - start) * 1000, 2)},
            )
            if candidate:
                candidates.append(candidate)
                continue
            rejected += 1
            if self._config.log_rejections:
                self._logger.log_reject(market.market_id, market.title, reason, details)
        self._log_info(
            "candidates_evaluated",
            {"count": len(candidates), "rejected": rejected},
        )
        if not candidates:
            return
        for candidate in candidates:
            if len(self._state.get_open_positions()) >= self._config.max_open_positions:
                break
            available = self._available_budget()
            size_usd = self._position_size(available)
            if size_usd <= 0:
                self._logger.log_skip(candidate.trade_id, "insufficient_budget")
                continue
            preview = self._approvals.format_preview(candidate, size_usd)
            self._approvals.send_preview(preview)
            if not self._approvals.await_approval(candidate.trade_id):
                continue
            if self._config.dry_run:
                quantity_wei = _estimate_quantity_wei(candidate.price, size_usd)
                self._logger.log_open(
                    candidate.trade_id,
                    candidate.market_id,
                    candidate.symbol,
                    candidate.side,
                    candidate.token_id,
                    quantity_wei,
                    size_usd,
                    candidate.price,
                    candidate.expiry_ts,
                )
                self._state.add_open_position(
                    Position(
                        trade_id=candidate.trade_id,
                        position_id=None,
                        market_id=candidate.market_id,
                        symbol=candidate.symbol,
                        side=candidate.side,
                        token_id=candidate.token_id,
                        quantity_wei=quantity_wei,
                        size_usd=size_usd,
                        entry_price=candidate.price,
                        opened_at_ts=int(time.time()),
                        expiry_ts=candidate.expiry_ts,
                        fee_rate_bps=candidate.fee_rate_bps,
                        is_neg_risk=candidate.is_neg_risk,
                        is_yield_bearing=candidate.is_yield_bearing,
                        decimal_precision=candidate.decimal_precision,
                    )
                )
                continue
            try:
                self._auth.ensure_jwt()
                self._orders.ensure_approvals()
                payload, quantity_wei, _ = self._orders.build_entry_order(candidate, size_usd)
                self._predictfun.create_order(payload)
                self._logger.log_open(
                    candidate.trade_id,
                    candidate.market_id,
                    candidate.symbol,
                    candidate.side,
                    candidate.token_id,
                    quantity_wei,
                    size_usd,
                    candidate.price,
                    candidate.expiry_ts,
                )
                self._state.add_open_position(
                    Position(
                        trade_id=candidate.trade_id,
                        position_id=None,
                        market_id=candidate.market_id,
                        symbol=candidate.symbol,
                        side=candidate.side,
                        token_id=candidate.token_id,
                        quantity_wei=quantity_wei,
                        size_usd=size_usd,
                        entry_price=candidate.price,
                        opened_at_ts=int(time.time()),
                        expiry_ts=candidate.expiry_ts,
                        fee_rate_bps=candidate.fee_rate_bps,
                        is_neg_risk=candidate.is_neg_risk,
                        is_yield_bearing=candidate.is_yield_bearing,
                        decimal_precision=candidate.decimal_precision,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                self._logger.log_error("place_order", str(exc))

    def run_once(self) -> None:
        self._log_info(
            "loop_start",
            {"open_positions": len(self._state.get_open_positions()), "budget": self._config.budget_total_usd},
        )
        markets = self._load_markets()
        if not markets:
            self._maybe_report_pnl()
            return
        if self._config.strategy_mode.lower() == "farm":
            try:
                self._auth.ensure_jwt()
                self._orders.ensure_approvals()
            except Exception as exc:  # noqa: BLE001
                self._logger.log_error("farm_auth", str(exc))
                return
            self._farm.run_once(markets)
            self._maybe_report_pnl()
            return
        markets_by_id = {market.market_id: market for market in markets}
        self._log_position_metrics(markets_by_id)
        self._handle_exits(markets_by_id)
        self._handle_entries(markets)
        self._maybe_report_pnl()
        self._log_info("loop_end", {"open_positions": len(self._state.get_open_positions())})

    def get_candidates(self, top_n: int) -> list:
        markets = self._load_markets()
        if not markets:
            return []
        momentum_probabilities: dict[str, float] = {}
        spot_prices: dict[str, float] = {}
        for symbol in self._config.allowed_symbols:
            try:
                momentum_probabilities[symbol] = binance.estimate_up_probability(
                    symbol,
                    self._config.binance_base_url,
                    self._config.binance_timeout_sec,
                    self._config.model_lookback_minutes,
                    self._config.model_k,
                )
            except Exception as exc:  # noqa: BLE001
                self._logger.log_error("binance_probability", str(exc))
            try:
                spot_prices[symbol] = binance.get_spot_price(
                    symbol,
                    self._config.binance_base_url,
                    self._config.binance_timeout_sec,
                )
            except Exception as exc:  # noqa: BLE001
                self._logger.log_error("binance_spot", str(exc))
        candidates = self._strategy.find_candidates(markets, momentum_probabilities, spot_prices)
        return candidates[:top_n]

    def run_forever(self) -> None:
        while True:
            self.run_once()
            time.sleep(self._config.poll_interval_sec)


def _estimate_quantity_wei(price: float, size_usd: float) -> int:
    if price <= 0:
        return 0
    quantity = size_usd / price
    return int(quantity * 1_000_000_000_000_000_000)
