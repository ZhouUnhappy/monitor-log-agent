# monitor-log-agent

[English](README.md)

只读 Claude Code agent 的示例：Python 宿主准备上下文，再用 [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview) 跑 agent 循环，最后把过程写成 HTML。

## 结构

| 部分 | 作用 |
| --- | --- |
| 宿主进程 | 读 `.env`、同步源码、拼 prompt、限制工具、渲染 HTML |
| Claude Code CLI | 由 SDK 拉起，真正跑 agent |
| skills 仓 | 文件系统 skill（`.claude/skills/`）。会话 `cwd` 指到这里 |
| 源码树 | `add_dirs` 挂进去，让 agent 做 Read / Grep / Glob |
| 自定义 MCP 工具 | 只读现场检查（`ssh_read`：参数数组 + host 白名单，不是一条 shell 字符串） |

不要给模型 Bash / Edit / Write。再用 `PreToolUse` hook 拒掉 Bash。`permission_mode="dontAsk"`，`strict_mcp_config=True`，只加载你传入的 MCP。

skill 放在旁边的仓，不塞进 runner。新的排查类型应该是新 skill + 源码/SSH 映射，而不是再写一个 agent。

## 隔离

SDK 默认 `setting_sources` 是 user + project + local，会读 `~/.claude/settings.json`。其中 `env` **会覆盖**进程环境（包括 `ANTHROPIC_BASE_URL` / `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL`）。

本项目设 `setting_sources=["project"]`，只加载 skills 仓的 `.claude/settings.json`。

宿主对本仓 `.env` 做 `load_dotenv`，再把 `ANTHROPIC_*` 放进 `ClaudeAgentOptions.env`。不需要 `uv run --env-file`。`load_dotenv` 不会覆盖已经在 shell 里的变量。

## 上限

20 turn，预算 `$5`（SDK 的 `total_cost_usd`，走第三方网关时不一定等于账单）。

## 依赖

- Python 3.11+、[uv](https://docs.astral.sh/uv/)
- 本机 [Claude Code CLI](https://code.claude.com/docs)

## 运行

```bash
uv run monitor-log-agent
```

结束时 stdout 会打 `cost=$... subtype=...`，报告在 `out/latest.html`。
