from __future__ import annotations

import json
import time
from urllib.parse import urlencode


class OrderbookWsClient:
    def __init__(self, base_url: str, api_key: str | None, timeout_sec: int) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._timeout_sec = timeout_sec

    def fetch_orderbooks(self, market_ids: list[str], snapshot_timeout_sec: float) -> dict[str, dict]:
        if not market_ids:
            return {}
        try:
            import websocket  # type: ignore
        except ImportError as exc:
            raise RuntimeError("Missing websocket-client dependency.") from exc

        url = self._base_url
        if self._api_key:
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}{urlencode({'apiKey': self._api_key})}"

        ws = websocket.create_connection(url, timeout=self._timeout_sec)
        ws.settimeout(1)
        try:
            self._subscribe(ws, market_ids)
            orderbooks = self._collect(ws, market_ids, snapshot_timeout_sec)
        finally:
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass
        return orderbooks

    @staticmethod
    def _subscribe(ws, market_ids: list[str]) -> None:
        topics = [f"predictOrderbook/{market_id}" for market_id in market_ids]
        payload = {"method": "subscribe", "requestId": int(time.time() * 1000), "params": topics}
        ws.send(json.dumps(payload))

    @staticmethod
    def _collect(ws, market_ids: list[str], timeout_sec: float) -> dict[str, dict]:
        end_time = time.time() + timeout_sec
        results: dict[str, dict] = {}
        remaining = set(market_ids)
        while time.time() < end_time and remaining:
            try:
                raw = ws.recv()
            except Exception:  # noqa: BLE001
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            msg_type = payload.get("type")
            if msg_type == "M":
                topic = payload.get("topic", "")
                if topic == "heartbeat":
                    ws.send(json.dumps({"method": "heartbeat", "data": payload.get("data")}))
                    continue
                if topic.startswith("predictOrderbook/"):
                    market_id = topic.split("/", 1)[1]
                    if market_id in remaining:
                        data = payload.get("data")
                        if isinstance(data, dict):
                            results[market_id] = data
                            remaining.discard(market_id)
        return results
