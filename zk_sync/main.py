"""
Entry point. Mirrors C# Program.cs.

Usage:
  python -m zk_sync                  (sync mode, poll every 60s)
  python -m zk_sync --poll 30        (custom poll interval)
  python -m zk_sync --fetch-only     (fetch + display + CSV, no ERPNext)
  python -m zk_sync --csv            (show CSV summary only)
  python -m zk_sync --test           (test device connectivity)
"""

import argparse
import logging
import os

from .config import POLL_SECONDS, LOG_DIR, DEVICES
from .engine import run_sync, fetch_only, test_connectivity, show_csv_summary


def setup_logging():
    """Configure console logging + per-device push log files."""
    os.makedirs(LOG_DIR, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Console handler — INFO level
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter(
        "%(asctime)s [%(threadName)s] %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
    ))
    root.addHandler(ch)

    # Per-device push loggers — only ERPNext push data
    for dev in DEVICES:
        device_id = dev["DeviceId"]
        push_logger = logging.getLogger(f"push.device{device_id}")
        push_logger.setLevel(logging.DEBUG)
        push_logger.propagate = False  # don't send to console/root

        fh = logging.FileHandler(
            os.path.join(LOG_DIR, f"device{device_id}_push.log"),
            encoding="utf-8",
        )
        fh.setFormatter(logging.Formatter(
            "%(asctime)s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        push_logger.addHandler(fh)


def main():
    parser = argparse.ArgumentParser(
        description="ZK -> ERPNext Sync (win32com + zkemkeeper COM SDK)")
    parser.add_argument("--csv", action="store_true",
                        help="Show CSV summary only")
    parser.add_argument("--fetch-only", action="store_true",
                        help="Fetch logs from devices, display + CSV, no ERPNext")
    parser.add_argument("--test", action="store_true",
                        help="Test device connectivity")
    parser.add_argument("--poll", type=int, default=POLL_SECONDS,
                        help=f"Poll interval seconds (default: {POLL_SECONDS})")
    args = parser.parse_args()

    setup_logging()

    if args.csv:
        show_csv_summary()
    elif args.fetch_only:
        fetch_only()
    elif args.test:
        test_connectivity()
    else:
        run_sync(max(args.poll, 5))


if __name__ == "__main__":
    main()
