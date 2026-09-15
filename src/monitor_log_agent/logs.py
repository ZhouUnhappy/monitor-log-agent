from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta

TOKEN_EXPIRE_CODE = 3600
SUCCESS_CODE = 200


@dataclass
class FetchLatestLogReq:
    api_base: str
    token: str
    log_id: int
    service_id: int
    lookback_days: int = 14
    timeout_sec: int = 20


@dataclass
class MonitorLog:
    id: int
    day: str
    service_ip: str
    service_name: str
    service_id: int
    log_id: int
    log: str
    log_times: str
    log_times_count: int


@dataclass
class PostJsonReq:
    url: str
    token: str
    body: dict
    timeout_sec: int = 20


@dataclass
class NormalizeDayReq:
    raw: str


def fetch_latest_log(req: FetchLatestLogReq) -> MonitorLog | None:
    end = date.today()
    start = end - timedelta(days=req.lookback_days)
    summary = _post_json(
        PostJsonReq(
            url=f"{req.api_base}/monitor-log/daily-summary",
            token=req.token,
            body={"start_date": start.isoformat(), "end_date": end.isoformat()},
            timeout_sec=req.timeout_sec,
        )
    )
    days = [
        normalize_day(NormalizeDayReq(raw=str(item.get("day") or "")))
        for item in summary.get("daily_summary") or []
        if item.get("day")
    ]
    days = sorted(set(day for day in days if day), reverse=True)
    if not days:
        days = [(end - timedelta(days=offset)).isoformat() for offset in range(req.lookback_days + 1)]

    for day in days:
        payload = _post_json(
            PostJsonReq(
                url=f"{req.api_base}/monitor-log/logs",
                token=req.token,
                body={"day": day, "service_id": req.service_id, "log_id": req.log_id},
                timeout_sec=req.timeout_sec,
            )
        )
        logs = payload.get("logs") or []
        if logs:
            return _to_monitor_log(logs[0])
    return None


def normalize_day(req: NormalizeDayReq) -> str:
    text = req.raw.strip()
    if not text:
        return ""
    if "T" in text:
        return datetime.fromisoformat(text).date().isoformat()
    return text


def _post_json(req: PostJsonReq) -> dict:
    data = json.dumps(req.body).encode("utf-8")
    request = urllib.request.Request(
        req.url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": req.token,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=req.timeout_sec) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {req.url}: {raw[:300]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"request {req.url} failed: {exc}") from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"non-JSON from {req.url}: {raw[:300]}") from exc

    meta = payload.get("meta_data") or {}
    code = int(meta.get("code") or 0)
    msg = str(meta.get("msg") or "")
    if code == TOKEN_EXPIRE_CODE:
        raise RuntimeError(f"monitor API token expired: {msg}")
    if code and code != SUCCESS_CODE:
        raise RuntimeError(f"monitor API error {code}: {msg}")
    return payload


def _to_monitor_log(row: dict) -> MonitorLog:
    return MonitorLog(
        id=int(row.get("id") or 0),
        day=str(row.get("day") or ""),
        service_ip=str(row.get("service_ip") or ""),
        service_name=str(row.get("service_name") or ""),
        service_id=int(row.get("service_id") or 0),
        log_id=int(row.get("log_id") or 0),
        log=str(row.get("log") or ""),
        log_times=str(row.get("log_times") or ""),
        log_times_count=int(row.get("log_times_count") or 0),
    )
