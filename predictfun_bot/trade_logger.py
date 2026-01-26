from __future__ import annotations

import json
from datetime import datetime, timezone
import os


class TradeLogger:
    def __init__(self, log_path: str) -> None:
        self._log_path = log_path

    def _write(self, payload: dict) -> None:
        payload["ts"] = datetime.now(timezone.utc).isoformat()
        directory = os.path.dirname(self._log_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(self._log_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=True) + "\n")

    def log_open(
        self,
        trade_id: str,
        market_id: str,
        symbol: str,
        side: str,
        token_id: str,
        quantity_wei: int,
        size_usd: float,
        entry_price: float,
        expiry_ts: int,
    ) -> None:
        self._write(
            {
                "event": "OPEN",
                "trade_id": trade_id,
                "market_id": market_id,
                "symbol": symbol,
                "side": side,
                "token_id": token_id,
                "quantity_wei": quantity_wei,
                "size_usd": size_usd,
                "entry_price": entry_price,
                "expiry_ts": expiry_ts,
            }
        )

    def log_close(
        self,
        trade_id: str,
        market_id: str,
        symbol: str,
        side: str,
        token_id: str,
        quantity_wei: int,
        size_usd: float,
        entry_price: float,
        exit_price: float,
        pnl_usd: float,
        reason: str,
    ) -> None:
        self._write(
            {
                "event": "CLOSE",
                "trade_id": trade_id,
                "market_id": market_id,
                "symbol": symbol,
                "side": side,
                "token_id": token_id,
                "quantity_wei": quantity_wei,
                "size_usd": size_usd,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "pnl_usd": pnl_usd,
                "reason": reason,
            }
        )

    def log_skip(self, trade_id: str, reason: str) -> None:
        self._write({"event": "SKIP", "trade_id": trade_id, "reason": reason})

    def log_error(self, context: str, message: str) -> None:
        self._write({"event": "ERROR", "context": context, "message": message})
