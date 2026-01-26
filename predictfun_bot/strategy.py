from __future__ import annotations

import time
import uuid

from .config import Config
from .models import Market, Position, TradeCandidate


class Strategy:
    def __init__(self, config: Config) -> None:
        self._config = config

    def _is_allowed_market(self, market: Market) -> bool:
        if market.symbol not in self._config.allowed_symbols:
            return False
        if market.resolution_minutes not in self._config.allowed_resolution_minutes:
            return False
        if market.kind not in self._config.allowed_kinds:
            return False
        return True

    def find_candidates(
        self,
        markets: list[Market],
        up_probability_by_symbol: dict[str, float],
        now_ts: int | None = None,
    ) -> list[TradeCandidate]:
        now_ts = now_ts or int(time.time())
        candidates: list[TradeCandidate] = []
        for market in markets:
            if not self._is_allowed_market(market):
                continue
            minutes_to_expiry = (market.expiry_ts - now_ts) / 60 if market.expiry_ts else 0
            if minutes_to_expiry > self._config.max_time_to_expiry_minutes:
                continue
            if minutes_to_expiry < self._config.min_time_to_expiry_minutes:
                continue
            if market.volume_usd < self._config.min_volume_usd:
                continue
            p_model = up_probability_by_symbol.get(market.symbol)
            if p_model is None:
                continue
            p_market = market.yes_price
            edge_yes = p_model - p_market
            edge_no = p_market - p_model
            if edge_yes >= self._config.edge_threshold:
                side = "YES"
                price = market.yes_price
                edge = edge_yes
            elif edge_no >= self._config.edge_threshold:
                side = "NO"
                price = market.no_price
                edge = edge_no
            else:
                continue
            if price <= 0:
                continue
            expected_roi = edge / price
            trade_id = uuid.uuid4().hex[:8]
            candidates.append(
                TradeCandidate(
                    trade_id=trade_id,
                    market_id=market.market_id,
                    symbol=market.symbol,
                    side=side,
                    price=price,
                    p_market=p_market,
                    p_model=p_model,
                    edge=edge,
                    expected_roi=expected_roi,
                    volume_usd=market.volume_usd,
                    expiry_ts=market.expiry_ts,
                )
            )
        candidates.sort(key=lambda item: item.edge, reverse=True)
        return candidates

    def should_exit(
        self,
        position: Position,
        market: Market,
        now_ts: int | None = None,
    ) -> tuple[bool, str, float]:
        now_ts = now_ts or int(time.time())
        minutes_to_expiry = (market.expiry_ts - now_ts) / 60 if market.expiry_ts else 0
        current_price = market.yes_price if position.side == "YES" else market.no_price
        if current_price <= 0 or position.entry_price <= 0:
            return True, "invalid_price", current_price
        roi = (current_price - position.entry_price) / position.entry_price
        if roi >= self._config.take_profit_pct:
            return True, "take_profit", current_price
        if roi <= -self._config.stop_loss_pct:
            return True, "stop_loss", current_price
        if minutes_to_expiry <= self._config.exit_before_expiry_minutes:
            return True, "expiry", current_price
        return False, "", current_price
