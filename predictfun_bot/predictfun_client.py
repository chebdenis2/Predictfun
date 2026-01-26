from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request


class PredictFunClient:
    def __init__(
        self,
        base_url: str,
        markets_path: str,
        orders_path: str,
        positions_path: str,
        balance_path: str,
        orderbook_path: str,
        stats_path: str,
        auth_message_path: str,
        auth_path: str,
        timeout_sec: int,
        api_key: str | None,
        api_key_header: str,
        auth_header: str,
        jwt_token: str | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._markets_path = markets_path
        self._orders_path = orders_path
        self._positions_path = positions_path
        self._balance_path = balance_path
        self._orderbook_path = orderbook_path
        self._stats_path = stats_path
        self._auth_message_path = auth_message_path
        self._auth_path = auth_path
        self._timeout_sec = timeout_sec
        self._api_key = api_key
        self._api_key_header = api_key_header
        self._auth_header = auth_header
        self._jwt_token = jwt_token

    def set_jwt(self, token: str | None) -> None:
        self._jwt_token = token

    def _require_base_url(self) -> None:
        if not self._base_url:
            raise RuntimeError("PREDICTFUN_BASE_URL is not configured.")

    def _build_url(self, path: str, params: dict | None = None) -> str:
        self._require_base_url()
        url = urllib.parse.urljoin(f"{self._base_url}/", path.lstrip("/"))
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        return url

    def _headers(self, require_auth: bool) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._api_key:
            headers[self._api_key_header] = self._api_key
        if require_auth:
            if not self._jwt_token:
                raise RuntimeError("JWT token is required but not set.")
            headers[self._auth_header] = f"Bearer {self._jwt_token}"
        return headers

    def _request_json(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        params: dict | None = None,
        require_auth: bool = False,
    ) -> object:
        data = None
        headers = self._headers(require_auth=require_auth)
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        url = self._build_url(path, params=params)
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

    @staticmethod
    def _format_path(template: str, market_id: str) -> str:
        return template.format(id=market_id)

    def list_markets(self, after: str | None = None, first: int | None = None) -> tuple[list[dict], str | None]:
        params: dict[str, str] = {}
        if after:
            params["after"] = str(after)
        if first:
            params["first"] = str(first)
        if not params:
            params = None
        payload = self._request_json("GET", self._markets_path, params=params)
        if not isinstance(payload, dict):
            raise RuntimeError(f"Unexpected markets payload: {payload}")
        if payload.get("success") is False:
            raise RuntimeError(f"Predict.fun markets error: {payload}")
        data = payload.get("data")
        cursor = payload.get("cursor")
        if not isinstance(data, list):
            raise RuntimeError(f"Unexpected markets data: {payload}")
        return data, cursor

    def get_all_markets(self, max_pages: int = 3, page_size: int | None = None) -> list[dict]:
        markets: list[dict] = []
        cursor = None
        for _ in range(max_pages):
            data, cursor = self.list_markets(cursor, page_size)
            markets.extend(data)
            if not cursor:
                break
        return markets

    def get_orderbook(self, market_id: str) -> dict:
        path = self._format_path(self._orderbook_path, market_id)
        payload = self._request_json("GET", path)
        if not isinstance(payload, dict) or "data" not in payload:
            raise RuntimeError(f"Unexpected orderbook payload: {payload}")
        if payload.get("success") is False:
            raise RuntimeError(f"Predict.fun orderbook error: {payload}")
        return payload["data"]

    def get_market_stats(self, market_id: str) -> dict:
        path = self._format_path(self._stats_path, market_id)
        payload = self._request_json("GET", path)
        if not isinstance(payload, dict) or "data" not in payload:
            raise RuntimeError(f"Unexpected stats payload: {payload}")
        if payload.get("success") is False:
            raise RuntimeError(f"Predict.fun stats error: {payload}")
        return payload["data"]

    def get_auth_message(self) -> str:
        payload = self._request_json("GET", self._auth_message_path)
        if not isinstance(payload, dict):
            raise RuntimeError(f"Unexpected auth message payload: {payload}")
        if payload.get("success") is False:
            raise RuntimeError(f"Predict.fun auth message error: {payload}")
        data = payload.get("data") or {}
        message = data.get("message")
        if not message:
            raise RuntimeError(f"Missing auth message: {payload}")
        return str(message)

    def post_auth(self, body: dict) -> str:
        payload = self._request_json("POST", self._auth_path, payload=body)
        if not isinstance(payload, dict):
            raise RuntimeError(f"Unexpected auth payload: {payload}")
        if payload.get("success") is False:
            raise RuntimeError(f"Predict.fun auth error: {payload}")
        data = payload.get("data") or {}
        token = data.get("token")
        if not token:
            raise RuntimeError(f"Missing auth token: {payload}")
        return str(token)

    def create_order(self, payload: dict) -> object:
        return self._request_json("POST", self._orders_path, payload=payload, require_auth=True)

    def list_positions(self) -> object:
        return self._request_json("GET", self._positions_path, require_auth=True)

    def get_account(self) -> object:
        return self._request_json("GET", self._balance_path, require_auth=True)
