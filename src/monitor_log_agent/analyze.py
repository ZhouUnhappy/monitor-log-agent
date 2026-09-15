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


@dataclass
class AnalyzeLatestRes:
    html_path: Path
    result: str


def main() -> None:
    asyncio.run(analyze_latest(AnalyzeLatestReq(config=load_config())))


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
        )
    )
    if log is None:
        raise RuntimeError(f"no monitor_log from API for log_id={cfg.log_id} service_id={cfg.service_id}")
    if not log.service_ip:
        raise RuntimeError(f"monitor_log id={log.id} has empty service_ip")

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

    async for message in query(
        prompt=_build_prompt(log, sync.commit, sync.message, cfg.er_controller_ssh.usernames),
        options=_build_options(cfg),
    ):
        _collect_event(events, message)
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

    html_path = write_html_report(
        WriteHtmlReq(
            output_path=cfg.output_dir / "latest.html",
            log=log,
            events=events,
            result=result_text,
            error=result_error,
            notes=f"everoute {sync.commit}: {sync.message}",
        )
    )
    print(f"\nhtml: {html_path}")
    return AnalyzeLatestRes(html_path=html_path, result=result_text)


def _build_prompt(log: MonitorLog, commit: str, sync_message: str, ssh_usernames: tuple[str, ...]) -> str:
    names = "、".join(ssh_usernames)
    return f"""排查下面这条 ms-controller（log_id=12）错误日志。先按 skill `ms-controller-triage` 做。

不要改任何文件，不要改 skill，不要用 Bash。现场信息只用 ssh_read，且 host 只能是这条日志的 service_ip。
SSH 目标是 **ER controller**（service_id=2）。运行时按用户名列表 [{names}] 顺序尝试，密码不要出现在 tool 参数里。
源码在已挂载的 everoute 仓库，当前 commit {commit}（{sync_message}）。进程名在代码里是 everoute-controller。

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


def _build_options(cfg: AppConfig) -> ClaudeAgentOptions:
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
