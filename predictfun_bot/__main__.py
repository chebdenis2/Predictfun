from __future__ import annotations

import argparse

from .approvals import ApprovalManager
from .auth import AuthManager
from .config import load_config
from .order_service import OrderService
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
        config.predictfun_orderbook_path,
        config.predictfun_stats_path,
        config.predictfun_auth_message_path,
        config.predictfun_auth_path,
        config.predictfun_timeout_sec,
        config.predictfun_api_key,
        config.predictfun_api_key_header,
        config.predictfun_auth_header,
        config.predictfun_jwt,
        config.predictfun_graphql_url,
    )
    state = StateStore(config.state_path)
    auth = AuthManager(config, client, state)
    approvals = ApprovalManager(config.approval_mode, config.approval_file_path, state)
    logger = TradeLogger(config.trade_log_path)
    strategy = Strategy(config)
    orders = OrderService(config)
    return Runner(config, client, auth, approvals, state, logger, strategy, orders)


def _print_top_candidates(top_n: int) -> None:
    runner = _build_runner()
    candidates = runner.get_candidates(top_n)
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
