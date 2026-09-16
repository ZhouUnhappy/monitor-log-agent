from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

MS_CONTROLLER_LOG_ID = 12
ER_CONTROLLER_SERVICE_ID = 2
DEFAULT_API_BASE = "http://172.21.150.196:30004"
ANTHROPIC_ENV_KEYS = (
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
)

AGENT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SKILLS_DIR = AGENT_ROOT.parent / "monitor-log-skills"
DEFAULT_EVEROUTE_SRC = Path("/Users/zhouxi/repositories/SmartxProject/everoute")


@dataclass
class ErControllerSsh:
    usernames: tuple[str, ...]
    password: str


@dataclass
class ParseStringListReq:
    raw: str
    fallback: tuple[str, ...]
    label: str = "value"


@dataclass
class SlackConfig:
    bot_token: str
    app_token: str
    channel_ids: frozenset[str]


@dataclass
class AppConfig:
    api_base: str
    api_token: str
    er_controller_ssh: ErControllerSsh
    everoute_src: Path
    skills_dir: Path
    output_dir: Path
    log_id: int
    service_id: int
    lookback_days: int
    skip_git_pull: bool
    anthropic_env: dict[str, str] = field(default_factory=dict)
    anthropic_model: str | None = None


def load_config() -> AppConfig:
    load_dotenv(AGENT_ROOT / ".env")

    api_token = os.getenv("MONITOR_TOKEN", "").strip()
    if not api_token:
        raise ValueError("MONITOR_TOKEN is empty; copy .env.example to .env and fill it in")

    anthropic_env = _load_anthropic_env()
    usernames = parse_string_list(
        ParseStringListReq(
            raw=os.getenv("ER_CONTROLLER_SSH_USERNAMES", ""),
            fallback=(),
            label="ER_CONTROLLER_SSH_USERNAMES",
        )
    )
    password = os.getenv("ER_CONTROLLER_SSH_PASSWORD", "").strip()
    if not usernames:
        raise ValueError("ER_CONTROLLER_SSH_USERNAMES is empty; copy .env.example to .env and fill it in")
    if not password:
        raise ValueError("ER_CONTROLLER_SSH_PASSWORD is empty; copy .env.example to .env and fill it in")

    return AppConfig(
        api_base=os.getenv("MONITOR_API_BASE", DEFAULT_API_BASE).strip().rstrip("/") or DEFAULT_API_BASE,
        api_token=api_token,
        er_controller_ssh=ErControllerSsh(
            usernames=usernames,
            password=password,
        ),
        everoute_src=DEFAULT_EVEROUTE_SRC,
        skills_dir=DEFAULT_SKILLS_DIR,
        output_dir=AGENT_ROOT / "out",
        log_id=MS_CONTROLLER_LOG_ID,
        service_id=ER_CONTROLLER_SERVICE_ID,
        lookback_days=int(os.getenv("MONITOR_LOOKBACK_DAYS", "14") or "14"),
        skip_git_pull=os.getenv("SKIP_GIT_PULL", "").strip().lower() in {"1", "true", "yes"},
        anthropic_env=anthropic_env,
        anthropic_model=anthropic_env.get("ANTHROPIC_MODEL") or None,
    )


def load_slack_config() -> SlackConfig:
    load_dotenv(AGENT_ROOT / ".env")
    bot_token = os.getenv("SLACK_BOT_TOKEN", "").strip()
    app_token = os.getenv("SLACK_APP_TOKEN", "").strip() or os.getenv("SLACK_APP_LEVEL_TOKEN", "").strip()
    channel_ids = parse_string_list(
        ParseStringListReq(
            raw=os.getenv("SLACK_CHANNEL_IDS", ""),
            fallback=(),
            label="SLACK_CHANNEL_IDS",
        )
    )
    if not bot_token:
        raise ValueError("SLACK_BOT_TOKEN is empty")
    if not bot_token.startswith("xoxb-"):
        raise ValueError("SLACK_BOT_TOKEN should start with xoxb-")
    if not app_token:
        raise ValueError("SLACK_APP_TOKEN or SLACK_APP_LEVEL_TOKEN is empty")
    if not app_token.startswith("xapp-"):
        raise ValueError("Slack app-level token should start with xapp-")
    if not channel_ids:
        raise ValueError("SLACK_CHANNEL_IDS is empty; add channel IDs like C0123456789")
    bad = [item for item in channel_ids if not _is_slack_channel_id(item)]
    if bad:
        raise ValueError(f"SLACK_CHANNEL_IDS must be Slack IDs (C…/G…), not names: {bad}")
    return SlackConfig(
        bot_token=bot_token,
        app_token=app_token,
        channel_ids=frozenset(channel_ids),
    )


def parse_string_list(req: ParseStringListReq) -> tuple[str, ...]:
    raw = req.raw.strip()
    if not raw:
        return req.fallback
    if raw.startswith("["):
        parsed = json.loads(raw)
        if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
            raise ValueError(f"{req.label} must be a JSON array of strings")
        names = tuple(item.strip() for item in parsed if item.strip())
    else:
        names = tuple(item.strip() for item in raw.split(",") if item.strip())
    return names or req.fallback


def _is_slack_channel_id(value: str) -> bool:
    if len(value) < 9:
        return False
    return value[0] in {"C", "G"} and value[1:].isalnum()


def _load_anthropic_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for key in ANTHROPIC_ENV_KEYS:
        value = os.getenv(key, "").strip()
        if value:
            env[key] = value
    return env
