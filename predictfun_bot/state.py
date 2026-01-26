from __future__ import annotations

import json
import os
from typing import Iterable

from .models import Position


class StateStore:
    def __init__(self, path: str) -> None:
        self._path = path
        self._state: dict = {"open_positions": {}, "seen_approvals": [], "last_report_date": None}
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self._path):
            return
        try:
            with open(self._path, "r", encoding="utf-8") as handle:
                self._state = json.load(handle)
        except (OSError, json.JSONDecodeError):
            self._state = {"open_positions": {}, "seen_approvals": [], "last_report_date": None}

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
                    size_usd=float(item["size_usd"]),
                    entry_price=float(item["entry_price"]),
                    opened_at_ts=int(item["opened_at_ts"]),
                    expiry_ts=int(item["expiry_ts"]),
                )
            )
        return positions

    def add_open_position(self, position: Position) -> None:
        self._state.setdefault("open_positions", {})[position.trade_id] = {
            "position_id": position.position_id,
            "market_id": position.market_id,
            "symbol": position.symbol,
            "side": position.side,
            "size_usd": position.size_usd,
            "entry_price": position.entry_price,
            "opened_at_ts": position.opened_at_ts,
            "expiry_ts": position.expiry_ts,
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
