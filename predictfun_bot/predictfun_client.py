from __future__ import annotations

import json
from datetime import datetime, timezone
import urllib.error
import urllib.parse
import urllib.request

from .models import Market


def _parse_ts(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        raw = value.strip()
        if raw.isdigit():
            return int(raw)
        try:
            raw = raw.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(raw)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            else:
                parsed = parsed.astimezone(timezone.utc)
            return int(parsed.timestamp())
        except ValueError:
            return 0
    return 0


def _pick(item: dict, keys: tuple[str, ...], default: object = None) -> object:
    for key in keys:
        if key in item and item[key] not in (None, ""):
            return item[key]
    return default


class PredictFunClient:
    def __init__(
        self,
        base_url: str,
        markets_path: str,
        orders_path: str,
        positions_path: str,
        balance_path: str,
        timeout_sec: int,
        api_key: str | None,
        api_secret: str | None,
        auth_header: str,
        auth_prefix: str,
        secret_header: str,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._markets_path = markets_path
        self._orders_path = orders_path
        self._positions_path = positions_path
        self._balance_path = balance_path
        self._timeout_sec = timeout_sec
        self._api_key = api_key
        self._api_secret = api_secret
        self._auth_header = auth_header
        self._auth_prefix = auth_prefix
        self._secret_header = secret_header

    def _require_base_url(self) -> None:
        if not self._base_url:
            raise RuntimeError("PREDICTFUN_BASE_URL is not configured.")

    def _build_url(self, path: str) -> str:
        self._require_base_url()
        return urllib.parse.urljoin(f"{self._base_url}/", path.lstrip("/"))

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._api_key:
            value = self._api_key
            if self._auth_prefix:
                value = f"{self._auth_prefix} {value}"
            headers[self._auth_header] = value
        if self._api_secret:
            headers[self._secret_header] = self._api_secret
        return headers

    def _request_json(self, method: str, url: str, payload: dict | None = None) -> object:
        data = None
        headers = self._headers()
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_sec) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Predict.fun request failed: {exc}") from exc
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid JSON from Predict.fun: {exc}") from exc

    def list_markets(self) -> list[Market]:
        url = self._build_url(self._markets_path)
        payload = self._request_json("GET", url)
        if isinstance(payload, dict):
            payload = payload.get("data") or payload.get("markets") or payload.get("items") or payload
        if not isinstance(payload, list):
            raise RuntimeError(f"Unexpected markets payload: {payload}")
        markets: list[Market] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            market_id = str(_pick(item, ("id", "market_id", "marketId"), ""))
            if not market_id:
                continue
            yes_price = float(_pick(item, ("yes_price", "yesPrice", "price_yes", "priceYes"), 0.0))
            no_price = float(_pick(item, ("no_price", "noPrice", "price_no", "priceNo"), 0.0))
            markets.append(
                Market(
                    market_id=market_id,
                    symbol=str(_pick(item, ("symbol", "ticker", "base"), "")),
                    title=str(_pick(item, ("title", "name"), "")),
                    yes_price=yes_price,
                    no_price=no_price,
                    volume_usd=float(_pick(item, ("volume_usd", "volumeUsd", "volume"), 0.0)),
                    expiry_ts=_parse_ts(_pick(item, ("expiry_ts", "expires_at", "expiry", "expiryTs"), 0)),
                    resolution_minutes=int(_pick(item, ("resolution_minutes", "resolutionMinutes"), 15)),
                    kind=str(_pick(item, ("kind", "type"), "UPDOWN")),
                    strike_price=_parse_strike(item),
                )
            )
        return markets


def _parse_strike(item: dict) -> float | None:
    value = _pick(
        item,
        (
            "strike_price",
            "strikePrice",
            "target_price",
            "targetPrice",
            "threshold",
            "start_price",
            "startPrice",
            "initial_price",
            "initialPrice",
        ),
        None,
    )
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

    def get_balance(self) -> object:
        url = self._build_url(self._balance_path)
        return self._request_json("GET", url)

    def list_positions(self) -> object:
        url = self._build_url(self._positions_path)
        return self._request_json("GET", url)

    def place_order(self, market_id: str, side: str, size_usd: float) -> object:
        url = self._build_url(self._orders_path)
        payload = {"market_id": market_id, "side": side, "size_usd": size_usd}
        return self._request_json("POST", url, payload)

    def close_position(self, position_id: str) -> object:
        url = self._build_url(f"{self._positions_path}/{position_id}/close")
        return self._request_json("POST", url, {})
