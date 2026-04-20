from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Iterable, Optional

import redis

from .models import AttendanceLog

log = logging.getLogger(__name__)


def _dt_to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        return dt.isoformat(sep=" ", timespec="seconds")
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _iso_to_dt(s: str) -> datetime:
    s = s.strip()
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


def _serialize_log(x: AttendanceLog) -> str:
    return json.dumps(
        {
            "source_ip": x.source_ip,
            "user_id": x.user_id,
            "timestamp": _dt_to_iso(x.timestamp),
            "log_type": x.log_type,
            "check_type": x.check_type,
            "verify_mode": x.verify_mode,
            "workcode": x.workcode,
            "device_id": x.device_id,
        },
        separators=(",", ":"),
    )


def _deserialize_log(s: str) -> AttendanceLog:
    d = json.loads(s)
    return AttendanceLog(
        source_ip=str(d["source_ip"]),
        user_id=str(d["user_id"]),
        timestamp=_iso_to_dt(str(d["timestamp"])),
        log_type=str(d["log_type"]),
        check_type=str(d["check_type"]),
        verify_mode=str(d["verify_mode"]),
        workcode=int(d.get("workcode", 0)),
        device_id=int(d["device_id"]),
    )


class RedisClient:
    """
    All keys are prefixed with `key_prefix` (e.g. "test:" or "att:") so
    multiple environments can share the same Redis instance safely.
    """

    def __init__(self, redis_url: str, key_prefix: str = "test:"):
        self._r = redis.Redis.from_url(redis_url, decode_responses=True)
        self._pfx = key_prefix

    def _k(self, key: str) -> str:
        return f"{self._pfx}{key}"

    def key_device_queue(self, device_ip: str) -> str:
        return self._k(f"device:{device_ip}:logs_queue")

    def key_global_queue(self) -> str:
        return self._k("global:logs_queue")

    def key_failed_queue(self) -> str:
        return self._k("global:failed_logs_queue")

    def key_failed_processing_queue(self) -> str:
        return self._k("global:failed_logs_processing_queue")

    # -- health ---------------------------------------------------------------

    def ping(self) -> bool:
        return bool(self._r.ping())

    # -- push / pop -----------------------------------------------------------

    def push_logs(self, device_ip: str, logs: Iterable[AttendanceLog]) -> int:
        device_q = self.key_device_queue(device_ip)
        global_q = self.key_global_queue()

        items: list[str] = [_serialize_log(x) for x in logs]
        if not items:
            return 0

        pipe = self._r.pipeline(transaction=False)
        pipe.rpush(device_q, *items)
        pipe.rpush(global_q, *items)
        pipe.execute()
        return len(items)

    def pop_all_global(self) -> list[AttendanceLog]:
        """Drain everything from the global queue atomically."""
        q = self.key_global_queue()
        pipe = self._r.pipeline(transaction=True)
        pipe.lrange(q, 0, -1)
        pipe.delete(q)
        results = pipe.execute()
        items: list[str] = results[0] or []

        out: list[AttendanceLog] = []
        for s in items:
            try:
                out.append(_deserialize_log(s))
            except Exception:
                log.exception("Bad log payload in Redis (skipping): %r", s)
        return out

    # -- failed queue ---------------------------------------------------------

    def push_failed(self, logs: Iterable[AttendanceLog]) -> int:
        q = self.key_failed_queue()
        items: list[str] = [_serialize_log(x) for x in logs]
        if not items:
            return 0
        self._r.rpush(q, *items)
        return len(items)

    def lease_failed(self, timeout_seconds: int = 2) -> Optional[str]:
        src = self.key_failed_queue()
        dst = self.key_failed_processing_queue()
        return self._r.brpoplpush(src, dst, timeout=timeout_seconds)

    def ack_failed(self, raw_item: str) -> None:
        q = self.key_failed_processing_queue()
        self._r.lrem(q, 1, raw_item)

    def requeue_failed(self, raw_item: str) -> None:
        src = self.key_failed_processing_queue()
        dst = self.key_failed_queue()
        pipe = self._r.pipeline(transaction=False)
        pipe.lrem(src, 1, raw_item)
        pipe.rpush(dst, raw_item)
        pipe.execute()
