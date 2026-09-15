from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from monitor_log_agent.logs import MonitorLog


@dataclass
class TranscriptEvent:
    kind: str
    text: str


@dataclass
class WriteHtmlReq:
    output_path: Path
    log: MonitorLog
    events: list[TranscriptEvent]
    result: str
    error: str = ""
    notes: str = ""


def write_html_report(req: WriteHtmlReq) -> Path:
    req.output_path.parent.mkdir(parents=True, exist_ok=True)
    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    events_html = "\n".join(_event_html(event) for event in req.events) or "<p>无过程输出</p>"
    result = req.result or req.error or "(empty)"
    body = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>ms-controller log {html.escape(str(req.log.id))}</title>
  <style>
    body {{ font-family: ui-sans-serif, system-ui, sans-serif; margin: 24px; color: #111; }}
    h1, h2 {{ margin-top: 1.4em; }}
    pre {{ background: #f6f6f6; padding: 12px; overflow: auto; white-space: pre-wrap; }}
    .meta td {{ padding: 4px 12px 4px 0; vertical-align: top; }}
    .tool {{ border-left: 3px solid #888; padding-left: 12px; margin: 12px 0; }}
    .assistant {{ border-left: 3px solid #2563eb; padding-left: 12px; margin: 12px 0; }}
  </style>
</head>
<body>
  <h1>ms-controller 日志排查</h1>
  <p>生成时间：{html.escape(generated)}</p>
  <p>{html.escape(req.notes) if req.notes else ""}</p>
  <h2>日志</h2>
  <table class="meta">
    <tr><td>id</td><td>{html.escape(str(req.log.id))}</td></tr>
    <tr><td>day</td><td>{html.escape(req.log.day)}</td></tr>
    <tr><td>service_ip</td><td>{html.escape(req.log.service_ip)}</td></tr>
    <tr><td>service_name</td><td>{html.escape(req.log.service_name)}</td></tr>
    <tr><td>service_id</td><td>{html.escape(str(req.log.service_id))}</td></tr>
    <tr><td>log_id</td><td>{html.escape(str(req.log.log_id))}</td></tr>
    <tr><td>times_count</td><td>{html.escape(str(req.log.log_times_count))}</td></tr>
  </table>
  <h3>log</h3>
  <pre>{html.escape(req.log.log)}</pre>
  <h3>log_times</h3>
  <pre>{html.escape(req.log.log_times)}</pre>
  <h2>结论</h2>
  <pre>{html.escape(result)}</pre>
  <h2>过程</h2>
  {events_html}
</body>
</html>
"""
    req.output_path.write_text(body, encoding="utf-8")
    return req.output_path


def _event_html(event: TranscriptEvent) -> str:
    css = "tool" if event.kind == "tool" else "assistant"
    return f'<div class="{css}"><h3>{html.escape(event.kind)}</h3><pre>{html.escape(event.text)}</pre></div>'
