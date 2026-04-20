from __future__ import annotations

import logging
import random
import threading
import time
from datetime import datetime, timedelta

from sqlalchemy.engine import Engine

from .config import VERIFY_MAP, DeviceConfig, Settings
from .file_loggers import create_device_file_logger
from .models import AttendanceLog
from .redis_client import RedisClient

log = logging.getLogger(__name__)

_REALISTIC_CHECK_TYPES = ["MODE=255"]
_VERIFY_KEYS = list(VERIFY_MAP.keys())


class MockDeviceWorker(threading.Thread):
    """
    Drop-in replacement for DeviceWorker that generates simulated
    attendance logs.  No COM SDK, no hardware access whatsoever.
    """

    def __init__(
        self,
        dev: DeviceConfig,
        settings: Settings,
        redis_client: RedisClient,
        engine: Engine,
    ):
        super().__init__(name=f"mock-{dev.label or dev.ip}", daemon=True)
        self.dev = dev
        self.settings = settings
        self.redis = redis_client
        self.engine = engine
        self._stop_evt = threading.Event()
        self._dlog = create_device_file_logger(dev)

    def stop(self) -> None:
        self._stop_evt.set()

    def run(self) -> None:
        poll = self.settings.poll_seconds
        cycle = 0

        while not self._stop_evt.is_set():
            cycle += 1
            now = datetime.now()
            batch_size = random.randint(1, 5)
            logs: list[AttendanceLog] = []

            for _ in range(batch_size):
                verify_key = random.choice(_VERIFY_KEYS)
                logs.append(
                    AttendanceLog(
                        source_ip=self.dev.ip,
                        user_id=f"SS{random.randint(1, 1200):05d}",
                        timestamp=now - timedelta(seconds=random.randint(0, 30)),
                        log_type=self.dev.log_type,
                        check_type=random.choice(_REALISTIC_CHECK_TYPES),
                        verify_mode=VERIFY_MAP[verify_key],
                        workcode=0,
                        device_id=self.dev.device_id,
                    )
                )

            pushed = self.redis.push_logs(self.dev.ip, logs)
            log.info(
                "[MOCK %s] cycle=%d generated=%d queued=%d",
                self.dev.ip, cycle, len(logs), pushed,
            )
            self._dlog.info(
                "POLL  cycle=%d mock_generated=%d queued=%d",
                cycle, len(logs), pushed,
            )
            for lg in logs:
                self._dlog.info(
                    "INGEST User=%s Time=%s LogType=%s DevId=%d",
                    lg.user_id,
                    lg.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    lg.log_type,
                    lg.device_id,
                )
            self._sleep_interruptible(poll)

    def _sleep_interruptible(self, seconds: int) -> None:
        end = time.time() + max(seconds, 1)
        while not self._stop_evt.is_set() and time.time() < end:
            time.sleep(0.2)
