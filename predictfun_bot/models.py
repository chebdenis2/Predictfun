from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Market:
    market_id: str
    symbol: str
    title: str
    yes_price: float
    no_price: float
    volume_usd: float
    expiry_ts: int
    resolution_minutes: int
    kind: str


@dataclass(frozen=True)
class TradeCandidate:
    trade_id: str
    market_id: str
    symbol: str
    side: str
    price: float
    p_market: float
    p_model: float
    edge: float
    expected_roi: float
    volume_usd: float
    expiry_ts: int


@dataclass(frozen=True)
class Position:
    trade_id: str
    position_id: str | None
    market_id: str
    symbol: str
    side: str
    size_usd: float
    entry_price: float
    opened_at_ts: int
    expiry_ts: int
