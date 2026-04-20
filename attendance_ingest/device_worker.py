from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta

import pythoncom
from sqlalchemy.engine import Engine

from .config import START_DATE, DeviceConfig, Settings
from .db import get_last_timestamp
from .device_sdk import ZkSdkClient
from .file_loggers import create_device_file_logger
from .redis_client import RedisClient

log = logging.getLogger(__name__)

_START_DT = datetime.strptime(START_DATE, "%Y-%m-%d %H:%M:%S")


class DeviceWorker(threading.Thread):
    def __init__(
        self,
        dev: DeviceConfig,
        settings: Settings,
        redis_client: RedisClient,
        engine: Engine,
    ):
        super().__init__(name=dev.label or dev.ip, daemon=True)
        self.dev = dev
        self.settings = settings
        self.redis = redis_client
        self.engine = engine
        self._stop_evt = threading.Event()
        self._cycle = 0
        self._clock_skew_buffer = timedelta(minutes=1)

        self._tag = f"[{dev.log_type}-{dev.device_id}]"
        self._dlog = create_device_file_logger(dev)

    def stop(self) -> None:
        self._stop_evt.set()

    def run(self) -> None:
        pythoncom.CoInitialize()
        try:
            self._run_inner()
        finally:
            pythoncom.CoUninitialize()

    _RECONNECT_INTERVAL = 1800  # force reconnect every 30 minutes

    def _run_inner(self) -> None:
        poll = self.settings.poll_seconds
        backoff = 5
        client: ZkSdkClient | None = None
        connected_at: float = 0.0

        while not self._stop_evt.is_set():
            try:
                if client is not None and (time.time() - connected_at) >= self._RECONNECT_INTERVAL:
                    log.info("%s Proactive reconnect (session age %.0fs)", self._tag, time.time() - connected_at)
                    self._dlog.info("STATE proactive_reconnect age=%.0fs", time.time() - connected_at)
                    try:
                        client.disconnect()
                    except Exception:
                        pass
                    client = None

                if client is None:
                    log.info("%s Connecting to %s:%d ...", self._tag, self.dev.ip, self.dev.port)
                    self._dlog.info("STATE connecting ip=%s port=%d", self.dev.ip, self.dev.port)
                    client = ZkSdkClient(self.dev)
                    if not client.connect():
                        raise RuntimeError("Device connect failed")
                    connected_at = time.time()
                    log.info("%s Connected", self._tag)
                    self._dlog.info("STATE connected ip=%s port=%d", self.dev.ip, self.dev.port)

                self._cycle += 1
                last_ts = get_last_timestamp(self.engine, self.dev.ip)
                start_time = last_ts if last_ts else _START_DT
                end_time = datetime.now() + self._clock_skew_buffer

                is_backfill = last_ts is None
                if is_backfill:
                    log.info("%s BACKFILL from %s", self._tag, START_DATE)
                    self._dlog.info(
                        "STATE backfill from=%s to=%s",
                        start_time.strftime("%Y-%m-%d %H:%M:%S"),
                        end_time.strftime("%Y-%m-%d %H:%M:%S"),
                    )

                logs = client.read_time_range(start_time=start_time, end_time=end_time)

                if logs:
                    pushed = self.redis.push_logs(self.dev.ip, logs)
                    log.info("%s Fetched %d -> Redis", self._tag, pushed)
                    w0 = start_time.strftime("%Y-%m-%d %H:%M:%S")
                    w1 = end_time.strftime("%Y-%m-%d %H:%M:%S")
                    self._dlog.info(
                        "POLL  cycle=%d fetched=%d queued=%d window=[%s -> %s]",
                        self._cycle, len(logs), pushed, w0, w1,
                    )
                    for lg in logs:
                        self._dlog.info(
                            "INGEST User=%s Time=%s LogType=%s DevId=%d",
                            lg.user_id,
                            lg.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                            lg.log_type,
                            lg.device_id,
                        )
                else:
                    self._dlog.info(
                        "POLL  cycle=%d fetched=0 window=[%s -> %s]",
                        self._cycle,
                        start_time.strftime("%Y-%m-%d %H:%M:%S"),
                        end_time.strftime("%Y-%m-%d %H:%M:%S"),
                    )

                backoff = 5
                self._sleep_interruptible(poll)
            except Exception as ex:
                log.error("%s Error: %s", self._tag, ex)
                self._dlog.error("FAIL  device_error reason=%s", ex, exc_info=True)
                try:
                    if client is not None:
                        client.disconnect()
                except Exception:
                    pass
                client = None
                self._dlog.info("STATE retry_in_seconds=%d", backoff)
                self._sleep_interruptible(backoff)
                backoff = min(backoff * 2, 60)

    def _sleep_interruptible(self, seconds: int) -> None:
        end = time.time() + max(seconds, 1)
        while not self._stop_evt.is_set() and time.time() < end:
            time.sleep(0.2)
