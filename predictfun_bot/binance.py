from __future__ import annotations

import json
import urllib.error
import urllib.request


def _get_json(url: str, timeout_sec: int) -> object:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            payload = response.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Binance request failed: {exc}") from exc
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON from Binance: {exc}") from exc


def get_spot_price(symbol: str, base_url: str, timeout_sec: int) -> float:
    url = f"{base_url}/api/v3/ticker/price?symbol={symbol}"
    data = _get_json(url, timeout_sec)
    if not isinstance(data, dict) or "price" not in data:
        raise RuntimeError(f"Unexpected Binance response: {data}")
    return float(data["price"])


def get_recent_return(
    symbol: str,
    interval: str,
    base_url: str,
    timeout_sec: int,
    lookback_candles: int = 2,
) -> float:
    url = f"{base_url}/api/v3/klines?symbol={symbol}&interval={interval}&limit={lookback_candles}"
    data = _get_json(url, timeout_sec)
    if not isinstance(data, list) or len(data) < 2:
        raise RuntimeError(f"Unexpected Binance klines response: {data}")
    prev_close = float(data[-2][4])
    last_close = float(data[-1][4])
    if prev_close <= 0:
        return 0.0
    return (last_close / prev_close) - 1.0


def estimate_up_probability(
    symbol: str,
    base_url: str,
    timeout_sec: int,
    lookback_minutes: int,
    k: float,
) -> float:
    interval = f"{lookback_minutes}m"
    recent_return = get_recent_return(symbol, interval, base_url, timeout_sec)
    raw = 0.5 + (k * recent_return)
    if raw < 0.0:
        return 0.0
    if raw > 1.0:
        return 1.0
    return raw
