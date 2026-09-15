# monitor-log-agent

[简体中文](README.zh.md)

An example of a **read-only** Claude Code agent: a Python host prepares context, then [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview) runs the agent loop and the host writes an HTML transcript.

## Shape

| Piece | Role |
| --- | --- |
| Host process | Load `.env`, sync source, build the prompt, cap tools, render HTML |
| Claude Code CLI | Spawned by the SDK; this is the actual agent |
| Skills repo | Filesystem skills (`.claude/skills/`). Session `cwd` points here |
| Source tree | Mounted with `add_dirs` so the agent can Read / Grep / Glob |
| Custom MCP tool | Read-only live checks (`ssh_read`: argv list + host allowlist, not a shell string) |

Do not give the model Bash, Edit, or Write. Deny Bash again with a `PreToolUse` hook. `permission_mode="dontAsk"` and `strict_mcp_config=True` so only the MCP servers you pass in are loaded.

Skills live in a sibling repo, not inside the runner. A new investigation type should be a new skill plus source/SSH mapping, not a new agent binary.

## Isolation

SDK default `setting_sources` is user + project + local. That loads `~/.claude/settings.json`, whose `env` **overwrites** process environment (including `ANTHROPIC_BASE_URL` / `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL`).

This project sets `setting_sources=["project"]` so only the skills repo’s `.claude/settings.json` is loaded.

The host calls `load_dotenv` on this repo’s `.env` and copies `ANTHROPIC_*` into `ClaudeAgentOptions.env`. You do not need `uv run --env-file`. `load_dotenv` does not override variables already in the shell.

## Limits

20 turns, `$5` `max_budget_usd` (SDK `total_cost_usd`; may not match a third-party bill).

## Requirements

- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- [Claude Code CLI](https://code.claude.com/docs) on `PATH`

## Run

```bash
uv run monitor-log-agent
```

Stdout prints `cost=$... subtype=...`. Report: `out/latest.html`.
