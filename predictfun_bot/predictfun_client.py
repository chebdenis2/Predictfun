from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
import time


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
        graphql_url: str | None = None,
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
        self._graphql_url = graphql_url

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
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8")
            except Exception:  # noqa: BLE001
                body = ""
            raise RuntimeError(f"Predict.fun request failed: HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Predict.fun request failed: {exc}") from exc
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid JSON from Predict.fun: {exc}") from exc

    def _graphql_request(self, query: str, variables: dict | None = None) -> object:
        if not self._graphql_url:
            raise RuntimeError("PREDICTFUN_GRAPHQL_URL is not configured.")
        payload = {"query": query}
        if variables:
            payload["variables"] = variables
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0",
            "Origin": "https://predict.fun",
            "Referer": "https://predict.fun/",
        }
        request = urllib.request.Request(
            self._graphql_url,
            data=data,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_sec) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8")
            except Exception:  # noqa: BLE001
                body = ""
            raise RuntimeError(f"Predict.fun GraphQL request failed: HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Predict.fun GraphQL request failed: {exc}") from exc
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid JSON from Predict.fun GraphQL: {exc}") from exc
        if isinstance(payload, dict) and payload.get("errors"):
            raise RuntimeError(f"Predict.fun GraphQL error: {payload['errors']}")
        return payload

    @staticmethod
    def _format_path(template: str, market_id: str) -> str:
        return template.format(id=market_id)

    def list_markets(
        self,
        after: str | None = None,
        first: int | None = None,
        status: str | None = None,
    ) -> tuple[list[dict], str | None]:
        params: dict[str, str] = {}
        if after:
            params["after"] = str(after)
        if first:
            params["first"] = str(_clamp_first(first))
        if status:
            params["status"] = status
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

    def get_all_markets(
        self,
        max_pages: int = 3,
        page_size: int | None = None,
        sleep_sec: float = 0.0,
        max_errors: int = 0,
        statuses: tuple[str, ...] | None = None,
    ) -> list[dict]:
        markets: list[dict] = []
        seen_ids: set[str] = set()
        cursor = None
        errors = 0
        status_list = tuple(statuses or ())
        if not status_list:
            status_list = (None,)
        for status in status_list:
            cursor = None
            for _ in range(max_pages):
                try:
                    data, cursor = self.list_markets(cursor, page_size, status)
                    for item in data:
                        market_id = str(item.get("id") or "")
                        if market_id and market_id in seen_ids:
                            continue
                        if market_id:
                            seen_ids.add(market_id)
                        markets.append(item)
                    if not cursor:
                        break
                    if sleep_sec > 0:
                        time.sleep(sleep_sec)
                except RuntimeError as exc:
                    errors += 1
                    if "HTTP 400" in str(exc) and status:
                        break
                    if "HTTP 403" in str(exc) or "HTTP 429" in str(exc):
                        break
                    if max_errors and errors >= max_errors:
                        break
                    if sleep_sec > 0:
                        time.sleep(sleep_sec)
        return markets

    def list_markets_graphql(
        self,
        after: str | None = None,
        first: int | None = None,
        is_resolved: bool | None = None,
    ) -> tuple[list[dict], str | None, bool]:
        query = (
            "query Markets($first:Int,$after:String,$isResolved:Boolean){"
            " markets(pagination:{first:$first, after:$after}, filter:{isResolved:$isResolved}){"
            "  edges{ cursor node{"
            "    id title question description status spreadThreshold shareThreshold "
            "    decimalPrecision makerFeeBps takerFeeBps isTradingEnabled "
            "    statistics{ totalLiquidityUsd volumeTotalUsd volume24hUsd } "
            "    outcomes{ name index onChainId status }"
            "  }} "
            "  pageInfo{ endCursor hasNextPage }"
            " } }"
        )
        variables = {"first": _clamp_first(first or 50)}
        if after:
            variables["after"] = after
        if is_resolved is not None:
            variables["isResolved"] = bool(is_resolved)
        payload = self._graphql_request(query, variables)
        data = payload.get("data", {}).get("markets", {})
        edges = data.get("edges") or []
        page_info = data.get("pageInfo") or {}
        items: list[dict] = []
        for edge in edges:
            node = edge.get("node")
            if not isinstance(node, dict):
                continue
            items.append(_normalize_graphql_market(node))
        return items, page_info.get("endCursor"), bool(page_info.get("hasNextPage"))

    def get_all_markets_graphql(
        self,
        max_pages: int = 3,
        page_size: int | None = None,
        sleep_sec: float = 0.0,
        is_resolved: bool | None = None,
    ) -> list[dict]:
        markets: list[dict] = []
        cursor = None
        for _ in range(max_pages):
            items, cursor, has_next = self.list_markets_graphql(cursor, page_size, is_resolved)
            markets.extend(items)
            if not has_next or not cursor:
                break
            if sleep_sec > 0:
                time.sleep(sleep_sec)
        return markets

    def get_market_details(self, market_id: str) -> dict:
        path = f"{self._markets_path.rstrip('/')}/{market_id}"
        payload = self._request_json("GET", path)
        if not isinstance(payload, dict) or "data" not in payload:
            raise RuntimeError(f"Unexpected market payload: {payload}")
        if payload.get("success") is False:
            raise RuntimeError(f"Predict.fun market error: {payload}")
        return payload["data"]


def _clamp_first(value: int) -> int:
    if value < 1:
        return 1
    if value > 150:
        return 150
    return value


def _normalize_graphql_market(node: dict) -> dict:
    stats = node.get("statistics") or {}
    outcomes = []
    for outcome in node.get("outcomes") or []:
        if not isinstance(outcome, dict):
            continue
        outcomes.append(
            {
                "name": outcome.get("name"),
                "indexSet": outcome.get("index"),
                "onChainId": outcome.get("onChainId"),
                "status": outcome.get("status"),
            }
        )
    fee_bps = node.get("takerFeeBps") or node.get("makerFeeBps") or 0
    return {
        "id": node.get("id"),
        "imageUrl": node.get("imageUrl"),
        "title": node.get("title"),
        "question": node.get("question"),
        "description": node.get("description"),
        "status": node.get("status"),
        "feeRateBps": fee_bps,
        "spreadThreshold": node.get("spreadThreshold"),
        "shareThreshold": node.get("shareThreshold"),
        "decimalPrecision": node.get("decimalPrecision"),
        "statistics": stats,
        "outcomes": outcomes,
        "isTradingEnabled": node.get("isTradingEnabled"),
    }

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

    def list_orders(
        self,
        status: str | None = None,
        after: str | None = None,
        first: int | None = None,
    ) -> tuple[list[dict], str | None]:
        params: dict[str, str] = {}
        if status:
            params["status"] = status
        if after:
            params["after"] = str(after)
        if first:
            params["first"] = str(first)
        if not params:
            params = None
        payload = self._request_json("GET", self._orders_path, params=params, require_auth=True)
        if not isinstance(payload, dict):
            raise RuntimeError(f"Unexpected orders payload: {payload}")
        if payload.get("success") is False:
            raise RuntimeError(f"Predict.fun orders error: {payload}")
        data = payload.get("data")
        cursor = payload.get("cursor")
        if not isinstance(data, list):
            raise RuntimeError(f"Unexpected orders data: {payload}")
        return data, cursor

    def remove_orders(self, order_ids: list[str]) -> object:
        payload = {"data": {"ids": order_ids}}
        return self._request_json("POST", "/v1/orders/remove", payload=payload, require_auth=True)

    def list_positions(self) -> object:
        return self._request_json("GET", self._positions_path, require_auth=True)

    def get_account(self) -> object:
        return self._request_json("GET", self._balance_path, require_auth=True)
