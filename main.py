"""
FastAPI app entrypoint for the real-time attendance ingestion system.

Run:
  python -m uvicorn main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI

from attendance_ingest.api import router as api_router
from attendance_ingest.runtime import Runtime

# ── Logging Setup ────────────────────────────────────────────────────
LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

CONSOLE_FORMAT = "%(asctime)s | %(message)s"
FILE_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)-28s | %(message)s"
DATE_FORMAT = "%H:%M:%S"
FILE_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter(CONSOLE_FORMAT, datefmt=DATE_FORMAT))
root_logger.addHandler(console_handler)

file_handler = RotatingFileHandler(
    os.path.join(LOG_DIR, "attendance.log"),
    maxBytes=10 * 1024 * 1024,
    backupCount=5,
    encoding="utf-8",
)
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(logging.Formatter(FILE_FORMAT, datefmt=FILE_DATE_FORMAT))
root_logger.addHandler(file_handler)

logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

log = logging.getLogger(__name__)

# ── FastAPI App ──────────────────────────────────────────────────────
app = FastAPI(title="Attendance Ingestion", version="1.0.0")
app.include_router(api_router)

runtime = Runtime()


@app.on_event("startup")
def _startup() -> None:
    runtime.start()


@app.on_event("shutdown")
def _shutdown() -> None:
    runtime.stop()
    log.info("Stopped. Goodbye.")
