from __future__ import annotations

import logging
import threading
from datetime import datetime

from sqlalchemy.engine import Engine

from .aggregator import AggregatorWorker
from .config import START_DATE, Settings, load_settings
from .db import create_mysql_engine, fetch_logs, get_last_timestamp, init_db
from .failed_retry_worker import FailedRetryWorker
from .redis_client import RedisClient

log = logging.getLogger(__name__)

_singleton_lock = threading.Lock()
_singleton: "Runtime | None" = None


def runtime_singleton() -> "Runtime":
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = Runtime()
        return _singleton


class Runtime:
    def __init__(self):
        global _singleton
        with _singleton_lock:
            _singleton = self

        self.settings: Settings | None = None
        self.redis: RedisClient | None = None
        self.engine: Engine | None = None
        self.device_workers: list[threading.Thread] = []
        self.aggregator: AggregatorWorker | None = None
        self.failed_retry: FailedRetryWorker | None = None
        self._started = False

    def start(self) -> None:
        if self._started:
            return

        self.settings = load_settings()
        mode = "TESTING" if self.settings.testing_mode else "PRODUCTION"

        log.info("=" * 50)
        log.info("  ATTENDANCE SYSTEM | %s | %d devices", mode, len(self.settings.devices))
        for d in self.settings.devices:
            log.info("  [%s-%d] %s (%s:%d)", d.log_type, d.device_id, d.label, d.ip, d.port)
        log.info("  Poll: %ds | Backfill from: %s", self.settings.poll_seconds, START_DATE)
        log.info("=" * 50)

        self.redis = RedisClient(self.settings.redis_url, key_prefix=self.settings.redis_key_prefix)
        self.engine = create_mysql_engine(self.settings.mysql_url)
        init_db(self.engine)
        log.info("Redis: OK | MySQL: OK")

        for d in self.settings.devices:
            last_ts = get_last_timestamp(self.engine, d.ip)
            tag = f"[{d.log_type}-{d.device_id}]"
            if last_ts:
                log.info("%s Resuming from %s", tag, last_ts.strftime("%Y-%m-%d %H:%M:%S"))
            else:
                log.info("%s First run — backfill from %s", tag, START_DATE)

        self.aggregator = AggregatorWorker(
            redis_client=self.redis,
            engine=self.engine,
        )
        self.aggregator.start()

        self.failed_retry = FailedRetryWorker(
            redis_client=self.redis,
            engine=self.engine,
        )
        self.failed_retry.start()

        if self.settings.testing_mode:
            from .mock_device_worker import MockDeviceWorker

            self.device_workers = [
                MockDeviceWorker(dev=d, settings=self.settings, redis_client=self.redis, engine=self.engine)
                for d in self.settings.devices
            ]
        else:
            from .device_worker import DeviceWorker

            self.device_workers = [
                DeviceWorker(dev=d, settings=self.settings, redis_client=self.redis, engine=self.engine)
                for d in self.settings.devices
            ]

        for w in self.device_workers:
            w.start()
        log.info("System RUNNING — %d device workers active", len(self.device_workers))

        self._started = True

    def stop(self) -> None:
        if not self._started:
            return
        log.info("Shutting down...")
        for w in self.device_workers:
            w.stop()  # type: ignore[attr-defined]
        if self.aggregator:
            self.aggregator.stop()
        if self.failed_retry:
            self.failed_retry.stop()

        for w in self.device_workers:
            w.join(timeout=5)
        if self.aggregator:
            self.aggregator.join(timeout=5)
        if self.failed_retry:
            self.failed_retry.join(timeout=5)
        self._started = False

    def fetch_logs(
        self,
        start_time: datetime,
        end_time: datetime,
        limit: int = 5000,
        pushed_to_erp: bool | None = None,
    ) -> list[dict]:
        if not self.engine:
            raise RuntimeError("Runtime not started")
        return fetch_logs(self.engine, start_time, end_time, limit=limit, pushed_to_erp=pushed_to_erp)
