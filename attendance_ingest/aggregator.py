from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

from sqlalchemy.engine import Engine

from .db import insert_logs
from .models import AttendanceLog
from .redis_client import RedisClient

log = logging.getLogger(__name__)


class AggregatorWorker(threading.Thread):
    def __init__(self, redis_client: RedisClient, engine: Engine):
        super().__init__(name="aggregator", daemon=True)
        self.redis = redis_client
        self.engine = engine
        self._stop_evt = threading.Event()
        self._total_inserted = 0

    def stop(self) -> None:
        self._stop_evt.set()

    def run(self) -> None:
        backoff = 1.0
        while not self._stop_evt.is_set():
            batch = self.redis.pop_all_global()
            if not batch:
                time.sleep(0.25)
                continue

            deduped: list[AttendanceLog] = []
            seen: set[tuple[str, str, datetime]] = set()
            for x in batch:
                k = x.dedupe_key()
                if k in seen:
                    continue
                seen.add(k)
                deduped.append(x)

            deduped.sort(key=lambda x: x.timestamp)

            try:
                actual = insert_logs(self.engine, deduped)
                self._total_inserted += actual
                log.info("[DB] +%d rows (total: %d)", actual, self._total_inserted)
                backoff = 1.0
            except Exception:
                log.error("[DB] Insert failed for %d rows — moved to retry queue", len(deduped))
                self.redis.push_failed(deduped)
                time.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
