from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Iterable


def _get_env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return float(value)


def _get_env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return int(value)


def _get_env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _get_env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value


def _get_env_optional(name: str) -> str | None:
    value = os.getenv(name)
    if value is None or value == "":
        return None
    return value


def _parse_csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _expand(path: str) -> str:
    return os.path.expanduser(path)


@dataclass(frozen=True)
class Config:
    budget_total_usd: float
    position_size_min_pct: float
    position_size_max_pct: float
    max_open_positions: int
    min_volume_usd: float
    edge_threshold: float
    take_profit_pct: float
    stop_loss_pct: float
    exit_before_expiry_minutes: int
    max_time_to_expiry_minutes: int
    min_time_to_expiry_minutes: int
    binance_base_url: str
    binance_timeout_sec: int
    predictfun_base_url: str
    predictfun_markets_path: str
    predictfun_orders_path: str
    predictfun_positions_path: str
    predictfun_balance_path: str
    predictfun_timeout_sec: int
    predictfun_api_key: str | None
    predictfun_api_secret: str | None
    predictfun_auth_header: str
    predictfun_auth_prefix: str
    predictfun_secret_header: str
    approval_mode: str
    approval_file_path: str
    state_path: str
    trade_log_path: str
    pnl_log_path: str
    pnl_report_hour_utc: int
    pnl_report_minute_utc: int
    poll_interval_sec: int
    dry_run: bool
    model_k: float
    model_lookback_minutes: int
    allowed_symbols: tuple[str, ...]
    allowed_resolution_minutes: tuple[int, ...]
    allowed_kinds: tuple[str, ...]


def load_config() -> Config:
    symbols_csv = _get_env_str("ALLOWED_SYMBOLS", "BTCUSDT,ETHUSDT")
    resolutions_csv = _get_env_str("ALLOWED_RESOLUTIONS", "15")
    kinds_csv = _get_env_str("ALLOWED_KINDS", "UPDOWN")
    return Config(
        budget_total_usd=_get_env_float("BUDGET_TOTAL_USD", 50.0),
        position_size_min_pct=_get_env_float("POSITION_SIZE_MIN_PCT", 0.10),
        position_size_max_pct=_get_env_float("POSITION_SIZE_MAX_PCT", 0.15),
        max_open_positions=_get_env_int("MAX_OPEN_POSITIONS", 1),
        min_volume_usd=_get_env_float("MIN_VOLUME_USD", 50000.0),
        edge_threshold=_get_env_float("EDGE_THRESHOLD", 0.04),
        take_profit_pct=_get_env_float("TAKE_PROFIT_PCT", 0.30),
        stop_loss_pct=_get_env_float("STOP_LOSS_PCT", 0.15),
        exit_before_expiry_minutes=_get_env_int("EXIT_BEFORE_EXPIRY_MINUTES", 5),
        max_time_to_expiry_minutes=_get_env_int("MAX_TIME_TO_EXPIRY_MINUTES", 24 * 60),
        min_time_to_expiry_minutes=_get_env_int("MIN_TIME_TO_EXPIRY_MINUTES", 1),
        binance_base_url=_get_env_str("BINANCE_BASE_URL", "https://api.binance.com"),
        binance_timeout_sec=_get_env_int("BINANCE_TIMEOUT_SEC", 10),
        predictfun_base_url=_get_env_str("PREDICTFUN_BASE_URL", ""),
        predictfun_markets_path=_get_env_str("PREDICTFUN_MARKETS_PATH", "/markets"),
        predictfun_orders_path=_get_env_str("PREDICTFUN_ORDERS_PATH", "/orders"),
        predictfun_positions_path=_get_env_str("PREDICTFUN_POSITIONS_PATH", "/positions"),
        predictfun_balance_path=_get_env_str("PREDICTFUN_BALANCE_PATH", "/balance"),
        predictfun_timeout_sec=_get_env_int("PREDICTFUN_TIMEOUT_SEC", 10),
        predictfun_api_key=_get_env_optional("PREDICTFUN_API_KEY"),
        predictfun_api_secret=_get_env_optional("PREDICTFUN_API_SECRET"),
        predictfun_auth_header=_get_env_str("PREDICTFUN_AUTH_HEADER", "Authorization"),
        predictfun_auth_prefix=_get_env_str("PREDICTFUN_AUTH_PREFIX", "Bearer"),
        predictfun_secret_header=_get_env_str("PREDICTFUN_SECRET_HEADER", "X-API-SECRET"),
        approval_mode=_get_env_str("APPROVAL_MODE", "file"),
        approval_file_path=_expand(_get_env_str("APPROVAL_FILE_PATH", "~/clawd-approvals.txt")),
        state_path=_expand(_get_env_str("STATE_PATH", "~/.clawd-state.json")),
        trade_log_path=_expand(_get_env_str("TRADE_LOG_PATH", "~/clawd-trades.log")),
        pnl_log_path=_expand(_get_env_str("PNL_LOG_PATH", "~/clawd-pnl.log")),
        pnl_report_hour_utc=_get_env_int("PNL_REPORT_HOUR_UTC", 0),
        pnl_report_minute_utc=_get_env_int("PNL_REPORT_MINUTE_UTC", 0),
        poll_interval_sec=_get_env_int("POLL_INTERVAL_SEC", 15),
        dry_run=_get_env_bool("DRY_RUN", True),
        model_k=_get_env_float("MODEL_K", 10.0),
        model_lookback_minutes=_get_env_int("MODEL_LOOKBACK_MINUTES", 5),
        allowed_symbols=_parse_csv(symbols_csv),
        allowed_resolution_minutes=tuple(int(x) for x in _parse_csv(resolutions_csv)),
        allowed_kinds=_parse_csv(kinds_csv),
    )
