from __future__ import annotations

import argparse

from . import binance
from .approvals import ApprovalManager
from .config import load_config
from .pnl import append_daily_report
from .predictfun_client import PredictFunClient
from .runner import Runner
from .state import StateStore
from .strategy import Strategy
from .trade_logger import TradeLogger


def _build_runner() -> Runner:
    config = load_config()
    client = PredictFunClient(
        config.predictfun_base_url,
        config.predictfun_markets_path,
        config.predictfun_orders_path,
        config.predictfun_positions_path,
        config.predictfun_balance_path,
        config.predictfun_timeout_sec,
        config.predictfun_api_key,
        config.predictfun_api_secret,
        config.predictfun_auth_header,
        config.predictfun_auth_prefix,
        config.predictfun_secret_header,
    )
    state = StateStore(config.state_path)
    approvals = ApprovalManager(config.approval_mode, config.approval_file_path, state)
    logger = TradeLogger(config.trade_log_path)
    strategy = Strategy(config)
    return Runner(config, client, approvals, state, logger, strategy)


def _print_top_candidates(top_n: int) -> None:
    config = load_config()
    client = PredictFunClient(
        config.predictfun_base_url,
        config.predictfun_markets_path,
        config.predictfun_orders_path,
        config.predictfun_positions_path,
        config.predictfun_balance_path,
        config.predictfun_timeout_sec,
        config.predictfun_api_key,
        config.predictfun_api_secret,
        config.predictfun_auth_header,
        config.predictfun_auth_prefix,
        config.predictfun_secret_header,
    )
    strategy = Strategy(config)
    markets = client.list_markets()
    momentum_probabilities = {}
    spot_prices = {}
    for symbol in config.allowed_symbols:
        momentum_probabilities[symbol] = binance.estimate_up_probability(
            symbol,
            config.binance_base_url,
            config.binance_timeout_sec,
            config.model_lookback_minutes,
            config.model_k,
        )
        spot_prices[symbol] = binance.get_spot_price(
            symbol,
            config.binance_base_url,
            config.binance_timeout_sec,
        )
    candidates = strategy.find_candidates(markets, momentum_probabilities, spot_prices)
    if not candidates:
        print("No candidates.")
        return
    for candidate in candidates[:top_n]:
        preview = ApprovalManager.format_preview(candidate, config.budget_total_usd * config.position_size_max_pct)
        print(preview)


def main() -> None:
    parser = argparse.ArgumentParser(description="Predict.fun autonomous trader bot")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("run", help="Run the bot forever")
    subparsers.add_parser("once", help="Run one evaluation loop")

    report_parser = subparsers.add_parser("report", help="Write daily P&L report")
    report_parser.add_argument("--date", help="Date in YYYY-MM-DD (UTC)")

    candidates_parser = subparsers.add_parser("candidates", help="Print top candidates")
    candidates_parser.add_argument("--top", type=int, default=3)

    args = parser.parse_args()

    if args.command == "report":
        report = append_daily_report(load_config().trade_log_path, load_config().pnl_log_path, args.date)
        print(report)
        return
    if args.command == "candidates":
        _print_top_candidates(args.top)
        return
    runner = _build_runner()
    if args.command == "once":
        runner.run_once()
        return
    runner.run_forever()


if __name__ == "__main__":
    main()
