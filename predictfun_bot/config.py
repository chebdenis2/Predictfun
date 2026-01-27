from __future__ import annotations

from dataclasses import dataclass
import os


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
    predictfun_orderbook_path: str
    predictfun_stats_path: str
    predictfun_auth_message_path: str
    predictfun_auth_path: str
    predictfun_max_pages: int
    predictfun_markets_page_size: int
    predictfun_page_sleep_sec: float
    predictfun_max_page_errors: int
    predictfun_markets_statuses: tuple[str, ...]
    predictfun_markets_source: str
    predictfun_graphql_url: str
    predictfun_graphql_is_resolved: bool
    predictfun_timeout_sec: int
    predictfun_api_key: str | None
    predictfun_api_key_header: str
    predictfun_jwt: str | None
    predictfun_auth_header: str
    predictfun_auth_mode: str
    predictfun_wallet_private_key: str | None
    predictfun_privy_wallet_private_key: str | None
    predictfun_predict_account_address: str | None
    predictfun_chain_id: int
    predictfun_set_approvals: bool
    approval_mode: str
    approval_file_path: str
    state_path: str
    trade_log_path: str
    pnl_log_path: str
    pnl_report_hour_utc: int
    pnl_report_minute_utc: int
    poll_interval_sec: int
    dry_run: bool
    order_expiry_minutes: int
    verbose_logs: bool
    log_rejections: bool
    max_spread: float
    min_expected_roi: float
    fee_edge_multiplier: float
    strategy_mode: str
    farm_min_spread: float
    farm_max_spread: float
    farm_min_hold_sec: int
    farm_min_volume_usd: float
    farm_max_volume_usd: float
    farm_order_usd: float
    farm_max_open_markets: int
    farm_avoid_minutes: int
    farm_max_volatility: float
    farm_vol_samples: int
    farm_top_levels: int
    orderbook_retry_count: int
    orderbook_retry_sleep_sec: float
    markets_cursor_ttl_sec: int
    model_k: float
    model_lookback_minutes: int
    spot_weight: float
    spot_sensitivity: float
    allowed_symbols: tuple[str, ...]
    allowed_resolution_minutes: tuple[int, ...]
    allowed_kinds: tuple[str, ...]
    allowed_statuses: tuple[str, ...]


def load_config() -> Config:
    symbols_csv = _get_env_str("ALLOWED_SYMBOLS", "BTCUSDT,ETHUSDT")
    resolutions_csv = _get_env_str("ALLOWED_RESOLUTIONS", "15")
    kinds_csv = _get_env_str("ALLOWED_KINDS", "UPDOWN")
    statuses_csv = _get_env_str("ALLOWED_STATUSES", "UNPAUSED,PRICE_PROPOSED")
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
        predictfun_base_url=_get_env_str("PREDICTFUN_BASE_URL", "https://api.predict.fun"),
        predictfun_markets_path=_get_env_str("PREDICTFUN_MARKETS_PATH", "/v1/markets"),
        predictfun_orders_path=_get_env_str("PREDICTFUN_ORDERS_PATH", "/v1/orders"),
        predictfun_positions_path=_get_env_str("PREDICTFUN_POSITIONS_PATH", "/v1/positions"),
        predictfun_balance_path=_get_env_str("PREDICTFUN_BALANCE_PATH", "/v1/account"),
        predictfun_orderbook_path=_get_env_str(
            "PREDICTFUN_ORDERBOOK_PATH", "/v1/markets/{id}/orderbook"
        ),
        predictfun_stats_path=_get_env_str("PREDICTFUN_STATS_PATH", "/v1/markets/{id}/stats"),
        predictfun_auth_message_path=_get_env_str(
            "PREDICTFUN_AUTH_MESSAGE_PATH", "/v1/auth/message"
        ),
        predictfun_auth_path=_get_env_str("PREDICTFUN_AUTH_PATH", "/v1/auth"),
        predictfun_max_pages=_get_env_int("PREDICTFUN_MAX_PAGES", 5),
        predictfun_markets_page_size=_get_env_int("PREDICTFUN_MARKETS_PAGE_SIZE", 50),
        predictfun_page_sleep_sec=_get_env_float("PREDICTFUN_PAGE_SLEEP_SEC", 0.35),
        predictfun_max_page_errors=_get_env_int("PREDICTFUN_MAX_PAGE_ERRORS", 2),
        predictfun_markets_statuses=_parse_csv(_get_env_str("PREDICTFUN_MARKETS_STATUSES", "")),
        predictfun_markets_source=_get_env_str("PREDICTFUN_MARKETS_SOURCE", "rest"),
        predictfun_graphql_url=_get_env_str(
            "PREDICTFUN_GRAPHQL_URL", "https://graphql.predict.fun/graphql"
        ),
        predictfun_graphql_is_resolved=_get_env_bool("PREDICTFUN_GRAPHQL_IS_RESOLVED", False),
        predictfun_timeout_sec=_get_env_int("PREDICTFUN_TIMEOUT_SEC", 10),
        predictfun_api_key=_get_env_optional("PREDICTFUN_API_KEY"),
        predictfun_api_key_header=_get_env_str("PREDICTFUN_API_KEY_HEADER", "x-api-key"),
        predictfun_jwt=_get_env_optional("PREDICTFUN_JWT"),
        predictfun_auth_header=_get_env_str("PREDICTFUN_AUTH_HEADER", "Authorization"),
        predictfun_auth_mode=_get_env_str("PREDICTFUN_AUTH_MODE", "predict"),
        predictfun_wallet_private_key=_get_env_optional("PREDICTFUN_WALLET_PRIVATE_KEY"),
        predictfun_privy_wallet_private_key=_get_env_optional("PREDICTFUN_PRIVY_WALLET_PRIVATE_KEY"),
        predictfun_predict_account_address=_get_env_optional("PREDICTFUN_PREDICT_ACCOUNT_ADDRESS"),
        predictfun_chain_id=_get_env_int("PREDICTFUN_CHAIN_ID", 56),
        predictfun_set_approvals=_get_env_bool("PREDICTFUN_SET_APPROVALS", False),
        approval_mode=_get_env_str("APPROVAL_MODE", "file"),
        approval_file_path=_expand(_get_env_str("APPROVAL_FILE_PATH", "~/clawd-approvals.txt")),
        state_path=_expand(_get_env_str("STATE_PATH", "~/.clawd-state.json")),
        trade_log_path=_expand(_get_env_str("TRADE_LOG_PATH", "~/clawd-trades.log")),
        pnl_log_path=_expand(_get_env_str("PNL_LOG_PATH", "~/clawd-pnl.log")),
        pnl_report_hour_utc=_get_env_int("PNL_REPORT_HOUR_UTC", 0),
        pnl_report_minute_utc=_get_env_int("PNL_REPORT_MINUTE_UTC", 0),
        poll_interval_sec=_get_env_int("POLL_INTERVAL_SEC", 15),
        dry_run=_get_env_bool("DRY_RUN", True),
        order_expiry_minutes=_get_env_int("ORDER_EXPIRY_MINUTES", 10),
        verbose_logs=_get_env_bool("VERBOSE_LOGS", False),
        log_rejections=_get_env_bool("LOG_REJECTIONS", True),
        max_spread=_get_env_float("MAX_SPREAD", 0.05),
        min_expected_roi=_get_env_float("MIN_EXPECTED_ROI", 0.02),
        fee_edge_multiplier=_get_env_float("FEE_EDGE_MULTIPLIER", 2.0),
        strategy_mode=_get_env_str("STRATEGY_MODE", "edge"),
        farm_min_spread=_get_env_float("FARM_MIN_SPREAD", 0.01),
        farm_max_spread=_get_env_float("FARM_MAX_SPREAD", 0.02),
        farm_min_hold_sec=_get_env_int("FARM_MIN_HOLD_SEC", 300),
        farm_min_volume_usd=_get_env_float("FARM_MIN_VOLUME_USD", 50000.0),
        farm_max_volume_usd=_get_env_float("FARM_MAX_VOLUME_USD", 150000.0),
        farm_order_usd=_get_env_float("FARM_ORDER_USD", 5.0),
        farm_max_open_markets=_get_env_int("FARM_MAX_OPEN_MARKETS", 3),
        farm_avoid_minutes=_get_env_int("FARM_AVOID_MINUTES", 15),
        farm_max_volatility=_get_env_float("FARM_MAX_VOLATILITY", 0.02),
        farm_vol_samples=_get_env_int("FARM_VOL_SAMPLES", 6),
        farm_top_levels=_get_env_int("FARM_TOP_LEVELS", 5),
        orderbook_retry_count=_get_env_int("ORDERBOOK_RETRY_COUNT", 2),
        orderbook_retry_sleep_sec=_get_env_float("ORDERBOOK_RETRY_SLEEP_SEC", 0.6),
        markets_cursor_ttl_sec=_get_env_int("MARKETS_CURSOR_TTL_SEC", 900),
        model_k=_get_env_float("MODEL_K", 10.0),
        model_lookback_minutes=_get_env_int("MODEL_LOOKBACK_MINUTES", 5),
        spot_weight=_get_env_float("SPOT_WEIGHT", 0.6),
        spot_sensitivity=_get_env_float("SPOT_SENSITIVITY", 10.0),
        allowed_symbols=_parse_csv(symbols_csv),
        allowed_resolution_minutes=tuple(int(x) for x in _parse_csv(resolutions_csv)),
        allowed_kinds=_parse_csv(kinds_csv),
        allowed_statuses=_parse_csv(statuses_csv),
    )
