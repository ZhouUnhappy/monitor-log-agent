from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

MENTION_RE = re.compile(r"<@[^>]+>")
ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")

USAGE = """用法：@机器人 分析 2026-09-16
也可以写：今天、昨天

同一条消息的线程里再 @ 我，可以追问（例如：是不是 apiserver 挂了）。
带新日期会开新排查。只分析 ms-controller（log_id=12）那天最近一条。"""


@dataclass
class ParseMentionReq:
    text: str
    today: date


@dataclass
class ParseMentionRes:
    day: str | None = None
    error: str | None = None
    need_usage: bool = False


def mention_body(text: str) -> str:
    return " ".join(MENTION_RE.sub(" ", text or "").split())


def parse_mention(req: ParseMentionReq) -> ParseMentionRes:
    text = MENTION_RE.sub(" ", req.text or "")
    dates = list(dict.fromkeys(ISO_DATE_RE.findall(text)))
    if len(dates) > 1:
        return ParseMentionRes(error="一次只能指定一天，例如：分析 2026-09-16")
    if dates:
        day = dates[0]
        try:
            datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            return ParseMentionRes(error=f"日期无效：{day}，请用 YYYY-MM-DD")
        return ParseMentionRes(day=day)

    has_today = ("今天" in text) or ("今日" in text)
    has_yesterday = ("昨天" in text) or ("昨日" in text)
    if has_today and has_yesterday:
        return ParseMentionRes(error="请只指定一天：今天 或 昨天")
    if has_today:
        return ParseMentionRes(day=req.today.isoformat())
    if has_yesterday:
        return ParseMentionRes(day=(req.today - timedelta(days=1)).isoformat())
    return ParseMentionRes(need_usage=True)
