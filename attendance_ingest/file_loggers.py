"""Shared RotatingFileHandler setup for per-device log files."""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

from .config import DeviceConfig

_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
LINE_FMT = "%(asctime)s %(message)s"
LINE_DATE_FMT = "%Y-%m-%d %H:%M:%S"


def create_device_file_logger(dev: DeviceConfig) -> logging.Logger:
    """One file per device: logs/device_{device_id}_{log_type}.log"""
    name = f"device_{dev.device_id}_{dev.log_type}"
    device_log = logging.getLogger(name)
    device_log.setLevel(logging.INFO)
    device_log.propagate = False
    if device_log.handlers:
        return device_log

    os.makedirs(_LOG_DIR, exist_ok=True)
    fh = RotatingFileHandler(
        os.path.join(_LOG_DIR, f"{name}.log"),
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    fh.setFormatter(logging.Formatter(LINE_FMT, datefmt=LINE_DATE_FMT))
    device_log.addHandler(fh)
    return device_log
