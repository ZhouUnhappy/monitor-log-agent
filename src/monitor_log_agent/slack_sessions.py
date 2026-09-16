from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta


SESSION_LIMIT = 50
SESSION_TTL = timedelta(days=7)
EVENT_DEDUPE_LIMIT = 256
EVENT_DEDUPE_TTL = timedelta(minutes=15)


@dataclass
class SlackThreadSession:
    session_id: str
    day: str
    service_ip: str
    log_id: int
    touched_at: datetime


@dataclass
class GetSessionReq:
    channel: str
    thread_ts: str
    now: datetime


@dataclass
class PutSessionReq:
    channel: str
    thread_ts: str
    session_id: str
    day: str
    service_ip: str
    log_id: int
    now: datetime


@dataclass
class DeleteSessionReq:
    channel: str
    thread_ts: str


@dataclass
class DedupeEventReq:
    key: str
    now: datetime


class SlackSessionStore:
    def __init__(self, limit: int = SESSION_LIMIT, ttl: timedelta = SESSION_TTL) -> None:
        self._limit = limit
        self._ttl = ttl
        self._items: OrderedDict[tuple[str, str], SlackThreadSession] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, req: GetSessionReq) -> SlackThreadSession | None:
        key = (req.channel, req.thread_ts)
        with self._lock:
            self._purge(req.now)
            item = self._items.get(key)
            if item is None:
                return None
            item.touched_at = req.now
            self._items.move_to_end(key)
            return item

    def put(self, req: PutSessionReq) -> None:
        key = (req.channel, req.thread_ts)
        with self._lock:
            self._purge(req.now)
            self._items[key] = SlackThreadSession(
                session_id=req.session_id,
                day=req.day,
                service_ip=req.service_ip,
                log_id=req.log_id,
                touched_at=req.now,
            )
            self._items.move_to_end(key)
            while len(self._items) > self._limit:
                self._items.popitem(last=False)

    def delete(self, req: DeleteSessionReq) -> None:
        with self._lock:
            self._items.pop((req.channel, req.thread_ts), None)

    def _purge(self, now: datetime) -> None:
        expired = [key for key, item in self._items.items() if now - item.touched_at > self._ttl]
        for key in expired:
            del self._items[key]


class EventDedupe:
    def __init__(self, limit: int = EVENT_DEDUPE_LIMIT, ttl: timedelta = EVENT_DEDUPE_TTL) -> None:
        self._limit = limit
        self._ttl = ttl
        self._seen: OrderedDict[str, datetime] = OrderedDict()
        self._lock = threading.Lock()

    def seen_or_add(self, req: DedupeEventReq) -> bool:
        with self._lock:
            cutoff = req.now - self._ttl
            stale = [key for key, seen_at in self._seen.items() if seen_at < cutoff]
            for key in stale:
                del self._seen[key]
            if req.key in self._seen:
                self._seen.move_to_end(req.key)
                self._seen[req.key] = req.now
                return True
            self._seen[req.key] = req.now
            self._seen.move_to_end(req.key)
            while len(self._seen) > self._limit:
                self._seen.popitem(last=False)
            return False
