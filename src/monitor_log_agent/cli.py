from __future__ import annotations

import argparse
import asyncio

from monitor_log_agent.analyze import AnalyzeLatestReq, analyze_latest
from monitor_log_agent.config import load_config, load_slack_config
from monitor_log_agent.slack_bot import RunSlackReq, run_slack


def main() -> None:
    parser = argparse.ArgumentParser(prog="monitor-log-agent")
    parser.add_argument("command", nargs="?", choices=["slack"], help="start Slack Socket Mode bot")
    args = parser.parse_args()
    if args.command == "slack":
        run_slack(RunSlackReq(config=load_config(), slack=load_slack_config()))
        return
    res = asyncio.run(analyze_latest(AnalyzeLatestReq(config=load_config())))
    if res.html_path is None:
        raise SystemExit(res.error or "no monitor_log")
