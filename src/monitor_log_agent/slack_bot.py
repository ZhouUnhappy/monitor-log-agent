from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from monitor_log_agent.analyze import AnalyzeLatestReq, AnalyzeLatestRes, analyze_latest
from monitor_log_agent.config import AppConfig, SlackConfig
from monitor_log_agent.slack_mention import USAGE, ParseMentionReq, mention_body, parse_mention
from monitor_log_agent.slack_sessions import (
    DeleteSessionReq,
    DedupeEventReq,
    EventDedupe,
    GetSessionReq,
    PutSessionReq,
    SlackSessionStore,
)
from monitor_log_agent.slack_thread import LoadThreadReq, load_thread

SLACK_TEXT_LIMIT = 8000
SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass
class RunSlackReq:
    config: AppConfig
    slack: SlackConfig


@dataclass
class HandleMentionReq:
    event: dict
    say: Any
    client: Any
    logger: logging.Logger
    config: AppConfig
    slack: SlackConfig
    lock: threading.Lock
    sessions: SlackSessionStore
    dedupe: EventDedupe
    bot_user_id: str


@dataclass
class FormatResultReq:
    day: str
    result: AnalyzeLatestRes
    follow_up: bool = False


def run_slack(req: RunSlackReq) -> None:
    logging.basicConfig(level=logging.INFO)
    lock = threading.Lock()
    sessions = SlackSessionStore()
    dedupe = EventDedupe()
    app = App(token=req.slack.bot_token)
    bot_user_id = str(app.client.auth_test()["user_id"])

    @app.middleware
    def log_incoming(body, logger, next):
        event = body.get("event") or {}
        text = str(event.get("text") or "").replace("\n", " ")[:120]
        logger.info(
            "incoming type=%s event=%s channel=%s text=%s",
            body.get("type"),
            event.get("type"),
            event.get("channel"),
            text,
        )
        return next()

    @app.event("app_mention")
    def process_mention(event, say, client, logger):
        handle_mention(
            HandleMentionReq(
                event=event,
                say=say,
                client=client,
                logger=logger,
                config=req.config,
                slack=req.slack,
                lock=lock,
                sessions=sessions,
                dedupe=dedupe,
                bot_user_id=bot_user_id,
            )
        )

    channels = ",".join(sorted(req.slack.channel_ids))
    print(f"slack socket mode listening, channels={channels}")
    SocketModeHandler(app, req.slack.app_token).start()


def handle_mention(req: HandleMentionReq) -> None:
    event = req.event
    channel = str(event.get("channel") or "")
    thread_ts = str(event.get("thread_ts") or event.get("ts") or "")
    now = datetime.now(SHANGHAI)

    def reply(text: str) -> None:
        req.say(text=text, thread_ts=thread_ts)

    if channel not in req.slack.channel_ids:
        req.logger.info("ignore mention from channel %s", channel)
        reply("这个频道未在 SLACK_CHANNEL_IDS 白名单里。")
        return

    event_key = f"{channel}:{event.get('ts') or ''}"
    if req.dedupe.seen_or_add(DedupeEventReq(key=event_key, now=now)):
        req.logger.info("duplicate mention %s", event_key)
        return

    parsed = parse_mention(
        ParseMentionReq(
            text=str(event.get("text") or ""),
            today=now.date(),
        )
    )
    if parsed.error:
        reply(parsed.error)
        return

    existing = req.sessions.get(GetSessionReq(channel=channel, thread_ts=thread_ts, now=now))
    thread = load_thread(
        LoadThreadReq(
            client=req.client,
            channel=channel,
            thread_ts=thread_ts,
            event_ts=str(event.get("ts") or ""),
            bot_user_id=req.bot_user_id,
        )
    )
    if thread.error:
        req.logger.info("load thread failed: %s", thread.error)
    follow_up = None
    day = parsed.day
    inferred = False
    if parsed.day:
        follow_up = None
    elif existing is not None:
        follow_up = mention_body(str(event.get("text") or "")) or "请继续排查并补充结论。"
        day = existing.day
    elif thread.day:
        day = thread.day
        inferred = True
        req.logger.info("inferred day=%s source=%s host=%s", thread.day, thread.day_source, thread.host)
    else:
        reply(USAGE)
        return

    queued = req.lock.locked()
    if queued:
        kind = f"追问 {day}" if follow_up else f"分析 {day}"
        reply(f"当前有分析任务在跑，你这条会排队。目标：{kind}")
    with req.lock:
        if follow_up:
            start = f"收到，继续排查 {day} 的那条 ms-controller 日志。"
        elif thread.host:
            start = f"收到，正在分析 {day} host={thread.host} 的 ms-controller 日志。"
        else:
            start = f"收到，正在分析 {day} 的最近一条 ms-controller 日志。"
        if queued:
            reply(f"轮到你了。{start}")
        else:
            reply(start)
        try:
            result = asyncio.run(
                analyze_latest(
                    AnalyzeLatestReq(
                        config=req.config,
                        day=day,
                        resume=existing.session_id if follow_up and existing is not None else None,
                        follow_up=follow_up,
                        slack_context=thread.excerpt,
                        preferred_host=thread.host,
                    )
                )
            )
            if inferred and result.log is None and day:
                prev = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
                req.logger.info("no log for %s, retry %s", day, prev)
                reply(f"{day} 没有日志，改试 {prev}。")
                day = prev
                result = asyncio.run(
                    analyze_latest(
                        AnalyzeLatestReq(
                            config=req.config,
                            day=day,
                            slack_context=thread.excerpt,
                            preferred_host=thread.host,
                        )
                    )
                )
        except Exception as exc:
            req.logger.exception("analyze failed")
            if follow_up:
                req.sessions.delete(DeleteSessionReq(channel=channel, thread_ts=thread_ts))
            reply(f"分析失败：{exc}")
            return
        if follow_up and result.error and not result.result:
            req.sessions.delete(DeleteSessionReq(channel=channel, thread_ts=thread_ts))
        elif result.session_id and result.log is not None:
            req.sessions.put(
                PutSessionReq(
                    channel=channel,
                    thread_ts=thread_ts,
                    session_id=result.session_id,
                    day=day,
                    service_ip=result.log.service_ip,
                    log_id=result.log.id,
                    now=datetime.now(SHANGHAI),
                )
            )
        reply(_format_result(FormatResultReq(day=day, result=result, follow_up=bool(follow_up))))
        if result.html_path is None:
            return
        try:
            req.client.files_upload_v2(
                channel=channel,
                thread_ts=thread_ts,
                file=str(result.html_path),
                filename=result.html_path.name,
                title=f"ms-controller {day}",
            )
        except Exception as exc:
            req.logger.exception("upload html failed")
            reply(f"结论已发出，但 HTML 上传失败：{exc}")


def _format_result(req: FormatResultReq) -> str:
    res = req.result
    if res.log is None:
        return res.error or f"{req.day} 没有找到日志"
    title = "追问完成" if req.follow_up else "分析完成"
    body = (res.result or res.error or "(empty)").strip()
    text = (
        f"{title}：{req.day} 最近一条 ms-controller 日志\n"
        f"id={res.log.id}  host={res.log.service_ip}  times={res.log.log_times_count}\n\n"
        f"{body}"
    )
    if len(text) > SLACK_TEXT_LIMIT:
        return text[: SLACK_TEXT_LIMIT - 16] + "\n…(过长已截断)"
    return text
