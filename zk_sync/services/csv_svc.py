"""
CSV service. Mirrors C# Services/CsvService.cs.
Saves attendance records to CSV and loads seen-keys for deduplication.
"""

import csv
import logging
import os

log = logging.getLogger(__name__)

CSV_HEADER = ["source_ip", "user_id", "timestamp", "log_type",
              "check_type", "verify_mode", "workcode", "device_id"]


def save_csv(records, csv_path):
    """Append records to the given CSV file."""
    file_exists = os.path.exists(csv_path) and os.path.getsize(csv_path) > 0
    with open(csv_path, "a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(CSV_HEADER)
        for r in records:
            writer.writerow([r.source_ip, r.user_id, r.timestamp, r.log_type,
                             r.check_type, r.verify_mode, r.workcode, r.device_id])
    log.info("CSV: saved %d records to %s", len(records), csv_path)


def load_seen_from_csv(state, csv_path):
    """Load already-seen record keys from CSV for deduplication."""
    if not os.path.exists(csv_path):
        return
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader, None)
        for parts in reader:
            if len(parts) >= 5:
                key = f"{parts[0]}|{parts[1]}|{parts[2]}|{parts[4]}"
                state.seen_records.add(key)


def print_csv_summary(state, csv_path):
    """Print CSV file summary. Mirrors C# CsvService.PrintSummary()."""
    if not os.path.exists(csv_path):
        log.warning("File not found: %s", csv_path)
        return

    total = 0
    users = set()
    earliest = None
    latest = None
    in_count = 0
    out_count = 0

    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader, None)
        for parts in reader:
            if len(parts) >= 4:
                total += 1
                users.add(parts[1])
                ts = parts[2]
                if earliest is None or ts < earliest:
                    earliest = ts
                if latest is None or ts > latest:
                    latest = ts
                if parts[3] == "IN":
                    in_count += 1
                elif parts[3] == "OUT":
                    out_count += 1

    log.info("Total records : %d  (IN: %d, OUT: %d)", total, in_count, out_count)
    log.info("Unique users  : %d", len(users))
    log.info("Earliest log  : %s", earliest or "N/A")
    log.info("Latest log    : %s", latest or "N/A")
    log.info("Synced to ERPNext : %d", len(state.pushed_records))
    log.info("Pending sync      : %d", total - len(state.pushed_records))
