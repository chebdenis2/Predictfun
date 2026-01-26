from __future__ import annotations

from datetime import datetime, timezone
import time

from . import binance
from .approvals import ApprovalManager
from .config import Config
from .models import Market, Position
from .pnl import append_daily_report
from .predictfun_client import PredictFunClient
from .state import StateStore
from .strategy import Strategy
from .trade_logger import TradeLogger


def _extract_position_id(payload: object) -> str | None:
    if isinstance(payload, dict):
        for key in ("position_id", "positionId", "id"):
            if key in payload and payload[key]:
                return str(payload[key])
        data = payload.get("data")
        if isinstance(data, dict):
            for key in ("position_id", "positionId", "id"):
                if key in data and data[key]:
                    return str(data[key])
    return None


class Runner:
    def __init__(
        self,
        config: Config,
        predictfun_client: PredictFunClient,
        approvals: ApprovalManager,
        state: StateStore,
        logger: TradeLogger,
        strategy: Strategy,
    ) -> None:
        self._config = config
        self._predictfun = predictfun_client
        self._approvals = approvals
        self._state = state
        self._logger = logger
        self._strategy = strategy

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

    def _handle_exits(self, markets_by_id: dict[str, Market]) -> None:
        for position in self._state.get_open_positions():
            market = markets_by_id.get(position.market_id)
            if market is None:
                continue
            should_exit, reason, current_price = self._strategy.should_exit(position, market)
            if not should_exit:
                continue
            pnl_usd = position.size_usd * (current_price / position.entry_price - 1.0)
            if self._config.dry_run:
                self._logger.log_close(
                    position.trade_id,
                    position.market_id,
                    position.symbol,
                    position.side,
                    position.size_usd,
                    position.entry_price,
                    current_price,
                    pnl_usd,
                    reason,
                )
                self._state.close_position(position.trade_id)
                continue
            try:
                position_id = position.position_id or position.market_id
                self._predictfun.close_position(position_id)
                self._logger.log_close(
                    position.trade_id,
                    position.market_id,
                    position.symbol,
                    position.side,
                    position.size_usd,
                    position.entry_price,
                    current_price,
                    pnl_usd,
                    reason,
                )
                self._state.close_position(position.trade_id)
            except Exception as exc:  # noqa: BLE001
                self._logger.log_error("close_position", str(exc))

    def _handle_entries(self, markets: list[Market]) -> None:
        if len(self._state.get_open_positions()) >= self._config.max_open_positions:
            return
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
                self._logger.log_open(
                    candidate.trade_id,
                    candidate.market_id,
                    candidate.symbol,
                    candidate.side,
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
                        size_usd=size_usd,
                        entry_price=candidate.price,
                        opened_at_ts=int(time.time()),
                        expiry_ts=candidate.expiry_ts,
                    )
                )
                continue
            try:
                response = self._predictfun.place_order(candidate.market_id, candidate.side, size_usd)
                position_id = _extract_position_id(response)
                self._logger.log_open(
                    candidate.trade_id,
                    candidate.market_id,
                    candidate.symbol,
                    candidate.side,
                    size_usd,
                    candidate.price,
                    candidate.expiry_ts,
                )
                self._state.add_open_position(
                    Position(
                        trade_id=candidate.trade_id,
                        position_id=position_id,
                        market_id=candidate.market_id,
                        symbol=candidate.symbol,
                        side=candidate.side,
                        size_usd=size_usd,
                        entry_price=candidate.price,
                        opened_at_ts=int(time.time()),
                        expiry_ts=candidate.expiry_ts,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                self._logger.log_error("place_order", str(exc))

    def run_once(self) -> None:
        try:
            markets = self._predictfun.list_markets()
        except Exception as exc:  # noqa: BLE001
            self._logger.log_error("list_markets", str(exc))
            return
        markets_by_id = {market.market_id: market for market in markets}
        self._handle_exits(markets_by_id)
        self._handle_entries(markets)
        self._maybe_report_pnl()

    def run_forever(self) -> None:
        while True:
            self.run_once()
            time.sleep(self._config.poll_interval_sec)
