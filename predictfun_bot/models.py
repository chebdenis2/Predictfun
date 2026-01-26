from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Outcome:
    name: str
    index_set: int
    token_id: str


@dataclass(frozen=True)
class Market:
    market_id: str
    symbol: str
    title: str
    question: str
    status: str
    yes_ask: float | None
    yes_bid: float | None
    no_ask: float | None
    no_bid: float | None
    volume_usd: float
    expiry_ts: int
    resolution_minutes: int
    kind: str
    strike_price: float | None
    fee_rate_bps: int
    is_neg_risk: bool
    is_yield_bearing: bool
    decimal_precision: int
    outcomes: tuple[Outcome, ...]


@dataclass(frozen=True)
class TradeCandidate:
    trade_id: str
    market_id: str
    symbol: str
    side: str
    token_id: str
    price: float
    spread: float | None
    p_market: float
    p_model: float
    edge: float
    required_edge: float
    effective_edge: float
    expected_roi: float
    volume_usd: float
    expiry_ts: int
    fee_rate_bps: int
    is_neg_risk: bool
    is_yield_bearing: bool
    decimal_precision: int


@dataclass(frozen=True)
class Position:
    trade_id: str
    position_id: str | None
    market_id: str
    symbol: str
    side: str
    token_id: str
    quantity_wei: int
    size_usd: float
    entry_price: float
    opened_at_ts: int
    expiry_ts: int
    fee_rate_bps: int
    is_neg_risk: bool
    is_yield_bearing: bool
    decimal_precision: int
