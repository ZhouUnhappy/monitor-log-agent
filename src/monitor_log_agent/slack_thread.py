from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from monitor_log_agent.slack_mention import ISO_DATE_RE, MENTION_RE

SHANGHAI = ZoneInfo("Asia/Shanghai")
URL_RE = re.compile(r"https?://\S+", re.I)
DOT_IP_RE = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")
DASH_IP_RE = re.compile(r"(?<![\d])(\d{1,3}(?:-\d{1,3}){3})(?![\d])")
STATUS_PREFIXES = (
    "收到，正在分析",
    "收到，继续排查",
    "当前有分析任务在跑",
    "轮到你了",
    "这个频道未在",
    "结论已发出，但 HTML",
    "用法：",
)
EXCERPT_LIMIT = 20


@dataclass
class LoadThreadReq:
    client: Any
    channel: str
    thread_ts: str
    event_ts: str
    bot_user_id: str


@dataclass
class SlackThreadContext:
    is_reply: bool
    day: str | None = None
    day_source: str = ""
    host: str | None = None
    excerpt: str = ""
    error: str | None = None


def load_thread(req: LoadThreadReq) -> SlackThreadContext:
    if not req.thread_ts or req.thread_ts == req.event_ts:
        return SlackThreadContext(is_reply=False)
    try:
        payload = req.client.conversations_replies(
            channel=req.channel,
            ts=req.thread_ts,
            limit=50,
            inclusive=True,
        )
    except Exception as exc:
        return SlackThreadContext(is_reply=True, error=str(exc))
    messages = payload.get("messages") or []
    if not messages:
        return SlackThreadContext(is_reply=True)
    parent_text = str(messages[0].get("text") or "")
    return SlackThreadContext(
        is_reply=True,
        day=_infer_day(parent_text, str(messages[0].get("ts") or req.thread_ts)),
        day_source=_day_source(parent_text),
        host=_extract_host(parent_text),
        excerpt=_excerpt(LoadExcerptReq(messages=messages, bot_user_id=req.bot_user_id)),
    )


def _infer_day(parent_text: str, parent_ts: str) -> str:
    iso = _first_iso_day(parent_text)
    if iso:
        return iso
    return _day_from_slack_ts(parent_ts)


def _day_source(parent_text: str) -> str:
    if _first_iso_day(parent_text):
        return "thread_text"
    return "thread_ts"


def _first_iso_day(text: str) -> str | None:
    dates = list(dict.fromkeys(ISO_DATE_RE.findall(text)))
    if len(dates) != 1:
        return None
    try:
        datetime.strptime(dates[0], "%Y-%m-%d")
    except ValueError:
        return None
    return dates[0]


def _day_from_slack_ts(ts: str) -> str:
    return datetime.fromtimestamp(float(ts), tz=SHANGHAI).date().isoformat()


def _extract_host(text: str) -> str | None:
    stripped = URL_RE.sub(" ", text)
    for raw in DASH_IP_RE.findall(stripped):
        ip = raw.replace("-", ".")
        if _valid_ipv4(ip):
            return ip
    dotted = [item for item in DOT_IP_RE.findall(stripped) if _valid_ipv4(item)]
    if dotted:
        return dotted[0]
    return None


def _valid_ipv4(value: str) -> bool:
    parts = value.split(".")
    if len(parts) != 4:
        return False
    return all(part.isdigit() and 0 <= int(part) <= 255 for part in parts)


@dataclass
class LoadExcerptReq:
    messages: list[dict]
    bot_user_id: str


def _excerpt(req: LoadExcerptReq) -> str:
    lines: list[str] = []
    for index, message in enumerate(req.messages):
        text = str(message.get("text") or "").strip()
        if not text:
            continue
        if any(text.startswith(prefix) for prefix in STATUS_PREFIXES):
            continue
        user = str(message.get("user") or "")
        is_bot = user == req.bot_user_id or bool(message.get("bot_id"))
        is_mention = f"<@{req.bot_user_id}>" in text
        if index > 0 and not is_bot and not is_mention:
            continue
        role = "告警" if index == 0 else ("助手" if is_bot else "用户")
        cleaned = " ".join(MENTION_RE.sub(" ", text).split())
        if not cleaned:
            continue
        lines.append(f"{role}: {cleaned}")
        if len(lines) >= EXCERPT_LIMIT:
            break
    return "\n".join(lines)
