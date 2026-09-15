from __future__ import annotations

import asyncio
from dataclasses import dataclass

from claude_agent_sdk import ToolAnnotations, create_sdk_mcp_server, tool

from monitor_log_agent.ssh_read import (
    RunSshReadReq,
    SshRuntime,
    ValidateSshReadReq,
    run_ssh_read,
    validate_ssh_read,
)

DIAG_SERVER_NAME = "diag"
SSH_READ_TOOL = "ssh_read"
SSH_READ_ALLOWED_TOOL = f"mcp__{DIAG_SERVER_NAME}__{SSH_READ_TOOL}"

_runtime: SshRuntime | None = None

SSH_READ_SCHEMA = {
    "type": "object",
    "properties": {
        "host": {
            "type": "string",
            "description": "Target host IP. Must equal the monitor_log service_ip.",
        },
        "argv": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Argument vector, not a shell string. Examples: [\"uptime\"], "
                "[\"systemctl\", \"status\", \"ms-controller\"], "
                "[\"tail\", \"-n\", \"80\", \"/var/log/everoute/ms-controller.log\"]."
            ),
        },
    },
    "required": ["host", "argv"],
}


@dataclass
class SetSshRuntimeReq:
    runtime: SshRuntime


def set_ssh_runtime(req: SetSshRuntimeReq) -> None:
    global _runtime
    _runtime = req.runtime


@tool(
    SSH_READ_TOOL,
    "Run a read-only diagnostic command over SSH on the ER controller host. argv is an argument list, not a shell string. Host must be the log's service_ip. Runtime tries ER_CONTROLLER_SSH_USERNAMES in order. Do not pass a password.",
    SSH_READ_SCHEMA,
    annotations=ToolAnnotations(readOnlyHint=True),
)
async def ssh_read_tool(args: dict) -> dict:
    if _runtime is None:
        return _tool_error("ssh runtime is not configured")

    validated = validate_ssh_read(
        ValidateSshReadReq(
            host=str(args.get("host", "")),
            argv=list(args.get("argv") or []),
            allowed_hosts=_runtime.allowed_hosts,
        )
    )
    if not validated.ok:
        return _tool_error(validated.error)

    try:
        result = await asyncio.to_thread(
            run_ssh_read,
            RunSshReadReq(
                host=str(args["host"]).strip(),
                argv=validated.argv,
                usernames=_runtime.usernames,
                password=_runtime.password,
                timeout_sec=_runtime.timeout_sec,
                use_sudo=validated.use_sudo,
                preferred_username=_runtime.resolved_username,
            ),
        )
    except Exception as exc:
        return _tool_error(f"ssh failed: {exc}")

    _runtime.resolved_username = result.username
    return {"content": [{"type": "text", "text": result.output}]}


def build_diag_server():
    return create_sdk_mcp_server(name=DIAG_SERVER_NAME, version="0.1.0", tools=[ssh_read_tool])


def _tool_error(message: str) -> dict:
    return {"content": [{"type": "text", "text": message}], "is_error": True}
