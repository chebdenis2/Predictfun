from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import re
from .models import Market, Outcome


_SYMBOLS = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
}


def build_market_base(item: dict) -> Market:
    market_id = str(item.get("id") or "")
    title = str(item.get("title") or "")
    question = str(item.get("question") or "")
    status = str(item.get("status") or "")
    outcomes = _parse_outcomes(item.get("outcomes"))
    symbol = _parse_symbol(title, question)
    resolution_minutes = _parse_resolution_minutes(title, question)
    strike_price = _parse_strike_price(title, question)
    expiry_ts = _parse_expiry_ts(title, question)
    kind = _infer_kind(outcomes, title, question)
    fee_rate_bps = int(item.get("feeRateBps") or 0)
    is_neg_risk = bool(item.get("isNegRisk"))
    is_yield_bearing = bool(item.get("isYieldBearing"))
    decimal_precision = int(item.get("decimalPrecision") or 2)
    return Market(
        market_id=market_id,
        symbol=symbol,
        title=title,
        question=question,
        status=status,
        yes_ask=None,
        yes_bid=None,
        no_ask=None,
        no_bid=None,
        volume_usd=0.0,
        expiry_ts=expiry_ts,
        resolution_minutes=resolution_minutes,
        kind=kind,
        strike_price=strike_price,
        fee_rate_bps=fee_rate_bps,
        is_neg_risk=is_neg_risk,
        is_yield_bearing=is_yield_bearing,
        decimal_precision=decimal_precision,
        outcomes=outcomes,
    )


def apply_orderbook(market: Market, orderbook: dict | None) -> Market:
    if not orderbook:
        return market
    asks = orderbook.get("asks") or []
    bids = orderbook.get("bids") or []
    yes_ask = asks[0][0] if asks else None
    yes_bid = bids[0][0] if bids else None
    no_ask = _complement_price(yes_bid, market.decimal_precision) if yes_bid is not None else None
    no_bid = _complement_price(yes_ask, market.decimal_precision) if yes_ask is not None else None
    return replace(
        market,
        yes_ask=yes_ask,
        yes_bid=yes_bid,
        no_ask=no_ask,
        no_bid=no_bid,
    )


def apply_stats(market: Market, stats: dict | None) -> Market:
    if not stats:
        return market
    volume = stats.get("volume24hUsd")
    if volume is None:
        volume = stats.get("volumeTotalUsd")
    if volume is None:
        volume = stats.get("totalLiquidityUsd")
    try:
        volume_usd = float(volume) if volume is not None else market.volume_usd
    except (TypeError, ValueError):
        volume_usd = market.volume_usd
    return replace(market, volume_usd=volume_usd)


def _parse_outcomes(raw: object) -> tuple[Outcome, ...]:
    outcomes: list[Outcome] = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "")
            token_id = str(item.get("onChainId") or "")
            index_set = int(item.get("indexSet") or 0)
            if token_id:
                outcomes.append(Outcome(name=name, index_set=index_set, token_id=token_id))
    return tuple(outcomes)


def _parse_symbol(*texts: str) -> str:
    combined = " ".join(texts).upper()
    for key, symbol in _SYMBOLS.items():
        if re.search(rf"\\b{re.escape(key)}\\b", combined):
            return symbol
    return ""


def _parse_resolution_minutes(*texts: str) -> int:
    combined = " ".join(texts).lower()
    match = re.search(r"(\\d{1,3})\\s*(?:-?\\s*)(?:min|mins|minute|minutes|m)\\b", combined)
    if match:
        return int(match.group(1))
    return 0


def _parse_strike_price(*texts: str) -> float | None:
    combined = " ".join(texts)
    candidates = []
    for match in re.finditer(r"\\$?([0-9]{1,3}(?:,[0-9]{3})+|[0-9]+(?:\\.[0-9]+)?)", combined):
        raw = match.group(1).replace(",", "")
        try:
            value = float(raw)
        except ValueError:
            continue
        if value >= 100:
            candidates.append(value)
    if not candidates:
        return None
    return max(candidates)


def _parse_expiry_ts(*texts: str) -> int:
    combined = " ".join(texts)
    patterns = [
        r"(\\d{4}-\\d{2}-\\d{2}[ T]\\d{2}:\\d{2}(?::\\d{2})?\\s*UTC)",
        r"([A-Za-z]{3,9}\\s+\\d{1,2},\\s+\\d{4}\\s+\\d{2}:\\d{2}\\s*UTC)",
    ]
    formats = [
        "%Y-%m-%d %H:%M UTC",
        "%Y-%m-%d %H:%M:%S UTC",
        "%Y-%m-%dT%H:%M UTC",
        "%Y-%m-%dT%H:%M:%S UTC",
        "%b %d, %Y %H:%M UTC",
        "%B %d, %Y %H:%M UTC",
    ]
    for pattern in patterns:
        match = re.search(pattern, combined)
        if not match:
            continue
        token = match.group(1).strip().replace("T", " ")
        for fmt in formats:
            try:
                dt = datetime.strptime(token, fmt)
            except ValueError:
                continue
            return int(dt.replace(tzinfo=timezone.utc).timestamp())
    return 0


def _infer_kind(outcomes: tuple[Outcome, ...], *texts: str) -> str:
    names = {outcome.name.lower() for outcome in outcomes if outcome.name}
    if {"yes", "no"}.issubset(names):
        return "YESNO"
    if {"up", "down"}.issubset(names):
        return "UPDOWN"
    combined = " ".join(texts).lower()
    if "up" in combined and "down" in combined:
        return "UPDOWN"
    return "BINARY"


def _complement_price(price: float, decimal_precision: int) -> float:
    factor = 10**decimal_precision
    return (factor - round(price * factor)) / factor
