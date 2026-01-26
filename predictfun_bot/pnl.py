from __future__ import annotations

import json
import os
from datetime import datetime, timezone


def _parse_date(value: str | None) -> str:
    if value:
        return value
    return datetime.now(timezone.utc).date().isoformat()


def compute_daily_pnl(log_path: str, date_str: str | None = None) -> dict:
    date_str = _parse_date(date_str)
    total_pnl = 0.0
    trades = 0
    if not os.path.exists(log_path):
        return {"date": date_str, "pnl_usd": 0.0, "trades": 0}
    with open(log_path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if payload.get("event") != "CLOSE":
                continue
            ts = payload.get("ts", "")
            if not ts.startswith(date_str):
                continue
            total_pnl += float(payload.get("pnl_usd", 0.0))
            trades += 1
    return {"date": date_str, "pnl_usd": total_pnl, "trades": trades}


def append_daily_report(log_path: str, report_path: str, date_str: str | None = None) -> dict:
    report = compute_daily_pnl(log_path, date_str)
    directory = os.path.dirname(report_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(report_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(report, ensure_ascii=True) + "\n")
    return report
