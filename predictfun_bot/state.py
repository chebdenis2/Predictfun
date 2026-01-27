from __future__ import annotations

import json
import os
import time

from .models import FarmOrder, Position


class StateStore:
    def __init__(self, path: str) -> None:
        self._path = path
        self._state: dict = {
            "open_positions": {},
            "seen_approvals": [],
            "last_report_date": None,
            "auth_jwt": None,
            "farm_orders": {},
            "market_history": {},
            "markets_cursors": {},
        }
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self._path):
            return
        try:
            with open(self._path, "r", encoding="utf-8") as handle:
                self._state = json.load(handle)
        except (OSError, json.JSONDecodeError):
            self._state = {
                "open_positions": {},
                "seen_approvals": [],
                "last_report_date": None,
                "auth_jwt": None,
                "farm_orders": {},
                "market_history": {},
                "markets_cursors": {},
            }

    def _save(self) -> None:
        directory = os.path.dirname(self._path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as handle:
            json.dump(self._state, handle, ensure_ascii=True, indent=2)

    def get_open_positions(self) -> list[Position]:
        positions = []
        for trade_id, item in self._state.get("open_positions", {}).items():
            positions.append(
                Position(
                    trade_id=trade_id,
                    position_id=item.get("position_id"),
                    market_id=item["market_id"],
                    symbol=item["symbol"],
                    side=item["side"],
                    token_id=item.get("token_id", ""),
                    quantity_wei=int(item.get("quantity_wei", 0)),
                    size_usd=float(item["size_usd"]),
                    entry_price=float(item["entry_price"]),
                    opened_at_ts=int(item["opened_at_ts"]),
                    expiry_ts=int(item["expiry_ts"]),
                    fee_rate_bps=int(item.get("fee_rate_bps", 0)),
                    is_neg_risk=bool(item.get("is_neg_risk", False)),
                    is_yield_bearing=bool(item.get("is_yield_bearing", False)),
                    decimal_precision=int(item.get("decimal_precision", 2)),
                )
            )
        return positions

    def add_open_position(self, position: Position) -> None:
        self._state.setdefault("open_positions", {})[position.trade_id] = {
            "position_id": position.position_id,
            "market_id": position.market_id,
            "symbol": position.symbol,
            "side": position.side,
            "token_id": position.token_id,
            "quantity_wei": position.quantity_wei,
            "size_usd": position.size_usd,
            "entry_price": position.entry_price,
            "opened_at_ts": position.opened_at_ts,
            "expiry_ts": position.expiry_ts,
            "fee_rate_bps": position.fee_rate_bps,
            "is_neg_risk": position.is_neg_risk,
            "is_yield_bearing": position.is_yield_bearing,
            "decimal_precision": position.decimal_precision,
        }
        self._save()

    def close_position(self, trade_id: str) -> None:
        if trade_id in self._state.get("open_positions", {}):
            self._state["open_positions"].pop(trade_id, None)
            self._save()

    def mark_approval_seen(self, trade_id: str) -> None:
        seen = set(self._state.get("seen_approvals", []))
        seen.add(trade_id)
        self._state["seen_approvals"] = sorted(seen)
        self._save()

    def has_seen_approval(self, trade_id: str) -> bool:
        return trade_id in set(self._state.get("seen_approvals", []))

    def get_last_report_date(self) -> str | None:
        return self._state.get("last_report_date")

    def set_last_report_date(self, date_str: str) -> None:
        self._state["last_report_date"] = date_str
        self._save()

    def get_auth_jwt(self) -> str | None:
        return self._state.get("auth_jwt")

    def set_auth_jwt(self, token: str | None) -> None:
        self._state["auth_jwt"] = token
        self._save()

    def get_farm_orders(self) -> dict:
        return dict(self._state.get("farm_orders", {}))

    def set_farm_order(self, market_id: str, side: str, order: FarmOrder) -> None:
        self._state.setdefault("farm_orders", {}).setdefault(market_id, {})[side] = {
            "market_id": order.market_id,
            "side": order.side,
            "order_id": order.order_id,
            "order_hash": order.order_hash,
            "price": order.price,
            "quantity_wei": order.quantity_wei,
            "placed_at_ts": order.placed_at_ts,
            "status": order.status,
        }
        self._save()

    def remove_farm_order(self, market_id: str, side: str) -> None:
        bucket = self._state.get("farm_orders", {}).get(market_id, {})
        if side in bucket:
            bucket.pop(side, None)
            if not bucket:
                self._state.get("farm_orders", {}).pop(market_id, None)
            self._save()

    def list_farm_orders(self) -> list[FarmOrder]:
        orders: list[FarmOrder] = []
        for market_id, sides in self._state.get("farm_orders", {}).items():
            for side, item in sides.items():
                orders.append(
                    FarmOrder(
                        market_id=market_id,
                        side=side,
                        order_id=item.get("order_id"),
                        order_hash=item.get("order_hash", ""),
                        price=float(item.get("price", 0.0)),
                        quantity_wei=int(item.get("quantity_wei", 0)),
                        placed_at_ts=int(item.get("placed_at_ts", 0)),
                        status=str(item.get("status", "")),
                    )
                )
        return orders

    def append_market_history(self, market_id: str, ts: int, mid_price: float, max_samples: int) -> None:
        history = self._state.setdefault("market_history", {}).setdefault(market_id, [])
        history.append({"ts": ts, "mid": mid_price})
        if len(history) > max_samples:
            history[:] = history[-max_samples:]
        self._save()

    def get_market_history(self, market_id: str) -> list[dict]:
        return list(self._state.get("market_history", {}).get(market_id, []))

    def get_markets_cursor(self, source: str, ttl_sec: int | None = None) -> str | None:
        record = self._state.get("markets_cursors", {}).get(source)
        if not isinstance(record, dict):
            return None
        cursor = record.get("cursor")
        ts = record.get("ts")
        if ttl_sec and ts and int(time.time()) - int(ts) > ttl_sec:
            return None
        return cursor

    def set_markets_cursor(self, source: str, cursor: str | None) -> None:
        if not cursor:
            return
        self._state.setdefault("markets_cursors", {})[source] = {
            "cursor": cursor,
            "ts": int(time.time()),
        }
        self._save()
