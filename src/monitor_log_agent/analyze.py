from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    HookMatcher,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    query,
)

from monitor_log_agent.config import AppConfig, load_config
from monitor_log_agent.git_sync import SyncRepoReq, sync_repo
from monitor_log_agent.html_report import TranscriptEvent, WriteHtmlReq, write_html_report
from monitor_log_agent.logs import FetchLatestLogReq, MonitorLog, fetch_latest_log
from monitor_log_agent.ssh_read import SshRuntime
from monitor_log_agent.tools import (
    SSH_READ_ALLOWED_TOOL,
    SetSshRuntimeReq,
    build_diag_server,
    set_ssh_runtime,
)


@dataclass
class AnalyzeLatestReq:
    config: AppConfig
    day: str | None = None
    html_name: str | None = None
    resume: str | None = None
    follow_up: str | None = None
    slack_context: str = ""
    preferred_host: str | None = None


@dataclass
class AnalyzeLatestRes:
    html_path: Path | None
    result: str
    log: MonitorLog | None = None
    error: str = ""
    session_id: str = ""


@dataclass
class HtmlNameReq:
    day: str | None
    log: MonitorLog


def main() -> None:
    res = asyncio.run(analyze_latest(AnalyzeLatestReq(config=load_config())))
    if res.html_path is None:
        raise RuntimeError(res.error or "no monitor_log")


async def analyze_latest(req: AnalyzeLatestReq) -> AnalyzeLatestRes:
    cfg = req.config
    if not cfg.everoute_src.is_dir():
        raise FileNotFoundError(f"EVEROUTE_SRC does not exist: {cfg.everoute_src}")
    if not (cfg.skills_dir / ".claude" / "skills").is_dir():
        raise FileNotFoundError(f"SKILLS_DIR has no .claude/skills: {cfg.skills_dir}")

    sync = sync_repo(SyncRepoReq(repo=cfg.everoute_src, skip=cfg.skip_git_pull))
    print(f"everoute {sync.commit}: {sync.message}")

    log = fetch_latest_log(
        FetchLatestLogReq(
            api_base=cfg.api_base,
            token=cfg.api_token,
            log_id=cfg.log_id,
            service_id=cfg.service_id,
            lookback_days=cfg.lookback_days,
            day=req.day,
            preferred_host=req.preferred_host,
        )
    )
    if log is None:
        day_hint = req.day or f"log_id={cfg.log_id} service_id={cfg.service_id}"
        return AnalyzeLatestRes(
            html_path=None,
            result="",
            error=f"没有找到 {day_hint} 的 ms-controller 日志",
        )
    if not log.service_ip:
        return AnalyzeLatestRes(
            html_path=None,
            result="",
            log=log,
            error=f"monitor_log id={log.id} has empty service_ip",
        )

    print(f"latest ms-controller log id={log.id} day={log.day} host={log.service_ip}")
    print(log.log[:500])

    set_ssh_runtime(
        SetSshRuntimeReq(
            runtime=SshRuntime(
                allowed_hosts=frozenset({log.service_ip}),
                usernames=cfg.er_controller_ssh.usernames,
                password=cfg.er_controller_ssh.password,
            )
        )
    )

    events: list[TranscriptEvent] = []
    result_text = ""
    result_error = ""
    session_id = ""

    prompt = (
        _build_follow_up_prompt(log, req.follow_up, cfg.er_controller_ssh.usernames)
        if req.follow_up
        else _build_prompt(log, sync.commit, sync.message, cfg.er_controller_ssh.usernames, req.slack_context)
    )
    async for message in query(
        prompt=prompt,
        options=_build_options(cfg, resume=req.resume),
    ):
        _collect_event(events, message)
        if getattr(message, "session_id", None):
            session_id = str(message.session_id)
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock) and block.text.strip():
                    print(block.text)
        elif isinstance(message, ResultMessage):
            result_text = message.result or ""
            if message.is_error:
                result_error = result_text or message.subtype
            print(
                f"cost=${message.total_cost_usd or 0:.4f} subtype={message.subtype}"
            )
            if result_text:
                print("\n===== result =====\n")
                print(result_text)

    html_name = req.html_name or _html_name(HtmlNameReq(day=req.day, log=log))
    html_path = write_html_report(
        WriteHtmlReq(
            output_path=cfg.output_dir / html_name,
            log=log,
            events=events,
            result=result_text,
            error=result_error,
            notes=f"everoute {sync.commit}: {sync.message}",
        )
    )
    print(f"\nhtml: {html_path}")
    return AnalyzeLatestRes(
        html_path=html_path,
        result=result_text,
        log=log,
        error=result_error,
        session_id=session_id,
    )


def _html_name(req: HtmlNameReq) -> str:
    if req.day is None:
        return "latest.html"
    day = req.day.replace("/", "-")
    return f"ms-controller-{day}-{req.log.id}.html"


def _build_prompt(
    log: MonitorLog,
    commit: str,
    sync_message: str,
    ssh_usernames: tuple[str, ...],
    slack_context: str = "",
) -> str:
    names = "、".join(ssh_usernames)
    slack = ""
    if slack_context.strip():
        slack = f"\nSlack 告警线程（宿主已取，日期和日志行已选定）：\n{slack_context}\n"
    return f"""排查下面这条 ms-controller（log_id=12）错误日志。先按 skill `ms-controller-triage` 做。

不要改任何文件，不要改 skill，不要用 Bash。现场信息只用 ssh_read，且 host 只能是这条日志的 service_ip。
SSH 目标是 **ER controller**（service_id=2）。运行时按用户名列表 [{names}] 顺序尝试，密码不要出现在 tool 参数里。
源码在已挂载的 everoute 仓库，当前 commit {commit}（{sync_message}）。进程名在代码里是 everoute-controller。
{slack}
monitor_log:
- id: {log.id}
- day: {log.day}
- service_ip: {log.service_ip}
- service_name: {log.service_name}
- service_id: {log.service_id}
- log_id: {log.log_id}
- log_times_count: {log.log_times_count}
- log_times:
{log.log_times}
- log:
{log.log}

最终用中文给出：结论、可能根因、代码线索、现场证据、建议（加白名单 / 真 bug / 需人工）。
"""


def _build_follow_up_prompt(log: MonitorLog, question: str, ssh_usernames: tuple[str, ...]) -> str:
    names = "、".join(ssh_usernames)
    return f"""这是同一条 ms-controller（log_id=12）排查的追问。继续按 skill `ms-controller-triage` 做。

不要改任何文件，不要改 skill，不要用 Bash。现场信息只用 ssh_read，且 host 只能是这条日志的 service_ip。
SSH 目标是 **ER controller**（service_id=2）。运行时按用户名列表 [{names}] 顺序尝试，密码不要出现在 tool 参数里。

monitor_log:
- id: {log.id}
- day: {log.day}
- service_ip: {log.service_ip}

追问：
{question}

最终用中文给出更新后的：结论、可能根因、代码线索、现场证据、建议（加白名单 / 真 bug / 需人工）。
"""


def _build_options(cfg: AppConfig, resume: str | None = None) -> ClaudeAgentOptions:
    env = {
        "CLAUDE_AGENT_SDK_CLIENT_APP": "monitor-log-agent/0.1.0",
        **cfg.anthropic_env,
    }
    return ClaudeAgentOptions(
        cwd=str(cfg.skills_dir),
        add_dirs=[str(cfg.everoute_src)],
        setting_sources=["project"],
        skills=["ms-controller-triage"],
        tools=["Read", "Grep", "Glob", "Skill"],
        allowed_tools=["Read", "Grep", "Glob", SSH_READ_ALLOWED_TOOL],
        disallowed_tools=["Bash", "Edit", "Write", "NotebookEdit"],
        permission_mode="dontAsk",
        strict_mcp_config=True,
        mcp_servers={"diag": build_diag_server()},
        max_turns=20,
        max_budget_usd=5,
        model=cfg.anthropic_model,
        resume=resume,
        system_prompt={
            "type": "preset",
            "preset": "claude_code",
            "append": (
                "This session is read-only. Never edit files or skills. "
                "Never use Bash. For live host checks call ssh_read with an argv list. "
                "Do not request or print SSH passwords."
            ),
        },
        hooks={
            "PreToolUse": [
                HookMatcher(matcher="Bash", hooks=[_deny_bash]),
            ]
        },
        env=env,
    )


async def _deny_bash(input_data, tool_use_id, context):
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "Bash is disabled; use ssh_read",
        }
    }


def _collect_event(events: list[TranscriptEvent], message: object) -> None:
    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, TextBlock) and block.text.strip():
                events.append(TranscriptEvent(kind="assistant", text=block.text))
            elif isinstance(block, ToolUseBlock):
                events.append(
                    TranscriptEvent(
                        kind="tool",
                        text=f"{block.name}\n{json.dumps(block.input, ensure_ascii=False, indent=2)}",
                    )
                )
    elif isinstance(message, ResultMessage) and message.result:
        events.append(TranscriptEvent(kind="result", text=message.result))
