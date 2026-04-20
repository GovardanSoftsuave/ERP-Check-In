from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

from sqlalchemy.engine import Engine

from .db import insert_logs
from .models import AttendanceLog
from .redis_client import RedisClient, _deserialize_log

log = logging.getLogger(__name__)


class FailedRetryWorker(threading.Thread):
    """
    Continuously drains Redis failed queue into MySQL.

    Reliability model:
    - Lease items using BRPOPLPUSH from `global:failed_logs_queue` to
      `global:failed_logs_processing_queue`.
    - On success: ACK by removing the leased payload from processing.
    - On DB failure: requeue back to failed and backoff.
    """

    def __init__(self, redis_client: RedisClient, engine: Engine):
        super().__init__(name="failed-retry", daemon=True)
        self.redis = redis_client
        self.engine = engine
        self._stop_evt = threading.Event()

    def stop(self) -> None:
        self._stop_evt.set()

    def run(self) -> None:
        backoff = 1.0
        while not self._stop_evt.is_set():
            leased: list[str] = []
            first = self.redis.lease_failed(timeout_seconds=2)
            if first:
                leased.append(first)
                while True:
                    nxt = self.redis.lease_failed(timeout_seconds=0)
                    if not nxt:
                        break
                    leased.append(nxt)

            if not leased:
                continue

            logs: list[AttendanceLog] = []
            bad_items: list[str] = []
            for raw in leased:
                try:
                    logs.append(_deserialize_log(raw))
                except Exception:
                    bad_items.append(raw)

            for raw in bad_items:
                log.error("Dropping malformed failed payload: %r", raw)
                self.redis.ack_failed(raw)

            if not logs:
                continue

            try:
                insert_logs(self.engine, logs)
                for raw in leased:
                    if raw in bad_items:
                        continue
                    self.redis.ack_failed(raw)
                backoff = 1.0
            except Exception:
                log.exception("Failed retry insert failed; requeueing batch")
                for raw in leased:
                    if raw in bad_items:
                        continue
                    self.redis.requeue_failed(raw)
                time.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
