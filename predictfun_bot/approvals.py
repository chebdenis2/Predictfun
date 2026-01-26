from __future__ import annotations

import os
from .models import TradeCandidate
from .state import StateStore


class ApprovalManager:
    def __init__(
        self,
        mode: str,
        approval_file_path: str,
        state: StateStore,
    ) -> None:
        self._mode = mode
        self._approval_file_path = approval_file_path
        self._state = state

    @staticmethod
    def format_preview(candidate: TradeCandidate, size_usd: float) -> str:
        return (
            f"[{candidate.trade_id}] market={candidate.market_id} symbol={candidate.symbol} "
            f"side={candidate.side} size_usd={size_usd:.2f} "
            f"p_market={candidate.p_market:.4f} p_model={candidate.p_model:.4f} "
            f"edge={candidate.edge:.4f} expected_roi={candidate.expected_roi:.2%}"
        )

    def send_preview(self, preview: str) -> None:
        print(preview)

    def await_approval(self, trade_id: str, poll_interval_sec: int = 5) -> bool:
        if self._mode == "interactive":
            while True:
                response = input(f"Type 'approve {trade_id}' to execute: ").strip()
                if response.lower() == f"approve {trade_id}".lower():
                    self._state.mark_approval_seen(trade_id)
                    return True
        if self._mode == "file":
            return self._check_file(trade_id)
        raise RuntimeError(f"Unknown approval mode: {self._mode}")

    def _check_file(self, trade_id: str) -> bool:
        if self._state.has_seen_approval(trade_id):
            return False
        if not os.path.exists(self._approval_file_path):
            return False
        with open(self._approval_file_path, "r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip().lower() == f"approve {trade_id}".lower():
                    self._state.mark_approval_seen(trade_id)
                    return True
        return False
