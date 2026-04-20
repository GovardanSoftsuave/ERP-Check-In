"""
Standalone ERP push script.
Reads pending attendance rows (pushed_to_erp=0) from MySQL and pushes them to ERPNext.
On success: ok/dup -> 1; no_employee -> 2 (excluded from future batches); errors stay 0 and retry.

Run:
  python erp_push.py

Pushes ERP_PUSH_BATCH_SIZE (50) records every ERP_PUSH_INTERVAL (5 min).
"""

from __future__ import annotations

import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler

import requests
from sqlalchemy import select, create_engine
from sqlalchemy.engine import Engine

from attendance_ingest.file_loggers import LINE_DATE_FMT, LINE_FMT
from attendance_ingest.config import (
    EMPLOYEE_ID_MAP,
    ERP_BULK_URL,
    ERP_PUSH_BATCH_SIZE,
    ERP_PUSH_INTERVAL,
    ERP_PUSH_MODE,
    ERP_SECRET_KEY,
    ERPNEXT_API_KEY,
    ERPNEXT_API_SECRET,
    ERPNEXT_URL,
    MYSQL_URL,
)
from attendance_ingest.db import (
    ERP_PUSH_PENDING,
    attendance_logs,
    mark_erp_no_employee,
    mark_pushed_to_erp,
)

# ── Logging ──────────────────────────────────────────────────────────
LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

log = logging.getLogger("erp_push")
log.setLevel(logging.INFO)

_line_fmt = logging.Formatter(LINE_FMT, datefmt=LINE_DATE_FMT)


class _QuietConsoleFilter(logging.Filter):
    """Terminal: only brief status lines; per-row OK/SKIP/FAIL stay in erp_push.log."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return msg.startswith("CFG ") or msg.startswith("CYCLE ") or msg.startswith("Stopped")


_console = logging.StreamHandler(sys.stdout)
_console.setFormatter(_line_fmt)
_console.addFilter(_QuietConsoleFilter())
log.addHandler(_console)

_file = RotatingFileHandler(
    os.path.join(LOG_DIR, "erp_push.log"),
    maxBytes=10 * 1024 * 1024,
    backupCount=5,
    encoding="utf-8",
)
_file.setFormatter(_line_fmt)
log.addHandler(_file)


def _mapped_employee_id(user_id: str) -> str:
    return str(EMPLOYEE_ID_MAP.get(user_id, user_id))


def _erp_rec_base(rec: dict) -> str:
    ts = rec["timestamp"].strftime("%Y-%m-%d %H:%M:%S")
    return (
        f"LogId={rec['id']} User={rec['user_id']} MappedId={_mapped_employee_id(rec['user_id'])} "
        f"Time={ts} LogType={rec['log_type']} DevId={rec['device_id']}"
    )


def _truncate_reason(s: str, max_len: int = 120) -> str:
    t = s.replace("\n", " ").replace("\r", " ").strip()
    if len(t) <= max_len:
        return t
    return t[: max_len - 3] + "..."


def _erp_log_ok(rec: dict, result: str) -> None:
    log.info("OK    %s Result=%s", _erp_rec_base(rec), result)


def _erp_log_skip(rec: dict, reason: str) -> None:
    log.info("SKIP  %s Reason=%s", _erp_rec_base(rec), _truncate_reason(reason, 80))


def _erp_log_fail(rec: dict, reason: str) -> None:
    log.info("FAIL  %s Result=error Reason=%s", _erp_rec_base(rec), _truncate_reason(reason))


def _erp_headers() -> dict[str, str]:
    return {
        "Authorization": f"token {ERPNEXT_API_KEY}:{ERPNEXT_API_SECRET}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _record_to_erp_payload(rec: dict) -> dict:
    """Single-row body fields (same shape for single and bulk items)."""
    user_id = rec["user_id"]
    mapped_id = EMPLOYEE_ID_MAP.get(user_id, user_id)
    return {
        "employee_field_value": mapped_id,
        "timestamp": rec["timestamp"].strftime("%Y-%m-%d %H:%M:%S"),
        "device_id": str(rec["device_id"]),
        "log_type": rec["log_type"],
    }


def _push_bulk(records: list[dict]) -> tuple[list[int], list[int], list[int], int]:
    """
    POST all rows in one request. Expects JSON like:
      { "results": [ {"ingest_id": <attendance_logs.id>, "status": "ok"|"duplicate"|"no_employee"|"error"}, ... ] }

    Fetches only pushed_to_erp=0. Caller marks ok/dup -> 1 and no_employee -> 2. Errors stay 0 and mix
    with the next oldest pending rows on the following cycle (ORDER BY timestamp).

    Returns: (ok_ids, dup_ids, skip_ids, err_count)
    """
    id_to_rec = {rec["id"]: rec for rec in records}
    items: list[dict] = []
    for rec in records:
        body = _record_to_erp_payload(rec)
        body["ingest_id"] = rec["id"]
        items.append(body)

    def _fail_all(reason: str) -> tuple[list[int], list[int], list[int], int]:
        for rec in records:
            _erp_log_fail(rec, reason)
        return [], [], [], len(items)

    try:
        resp = requests.post(
            ERP_BULK_URL,
            json={"secret_key": ERP_SECRET_KEY, "logs": items},
            headers=_erp_headers(),
            timeout=120,
        )
    except requests.exceptions.RequestException as ex:
        return _fail_all(f"network:{ex}")

    ok_ids: list[int] = []
    dup_ids: list[int] = []
    skip_ids: list[int] = []
    err_count = 0

    if resp.status_code not in (200, 201):
        snippet = _truncate_reason(resp.text[:500], 200)
        return _fail_all(f"HTTP_{resp.status_code}:{snippet}")

    try:
        data = resp.json()
    except ValueError:
        return _fail_all(f"bad_json:{_truncate_reason(resp.text[:300], 120)}")

    results = data.get("results")
    if results is None and isinstance(data.get("message"), dict):
        results = data["message"].get("results")
    if not isinstance(results, list):
        keys = list(data.keys()) if isinstance(data, dict) else type(data)
        return _fail_all(f"unexpected_response_keys:{keys}")

    for row in results:
        if not isinstance(row, dict):
            continue
        rid = row.get("ingest_id") or row.get("id")
        status = (row.get("status") or row.get("result") or "").lower()
        if rid is None:
            continue
        rid = int(rid)
        rec = id_to_rec.get(rid)
        if status in ("ok", "success", "created"):
            ok_ids.append(rid)
            if rec:
                _erp_log_ok(rec, "ok")
        elif status in ("duplicate", "dup", "exists"):
            dup_ids.append(rid)
            if rec:
                _erp_log_ok(rec, "duplicate")
        elif status in ("no_employee", "unknown_employee", "skip"):
            skip_ids.append(rid)
            if rec:
                detail = row.get("detail") or row.get("message") or ""
                reason = str(detail).strip() or "no_employee"
                _erp_log_skip(rec, reason)
        else:
            err_count += 1
            if rec:
                _erp_log_fail(rec, f"erp_status={status!r}")

    sent_ids = {it["ingest_id"] for it in items}
    accounted = set(ok_ids) | set(dup_ids) | set(skip_ids)
    missing = sent_ids - accounted
    if missing:
        err_count += len(missing)
        for mid in sorted(missing):
            rec = id_to_rec.get(mid)
            if rec:
                _erp_log_fail(rec, "response_missing")

    return ok_ids, dup_ids, skip_ids, err_count


def _push_one(record: dict) -> tuple[str, str | None]:
    """Push one record to ERPNext. Returns (status, fail_reason_or_none)."""
    url = (
        ERPNEXT_URL.rstrip("/")
        + "/api/method/hrms.hr.doctype.employee_checkin.employee_checkin"
          ".add_log_based_on_employee_field"
    )
    payload = _record_to_erp_payload(record)

    try:
        resp = requests.post(url, json=payload, headers=_erp_headers(), timeout=30)
        if resp.status_code in (200, 201):
            return "ok", None
        body = resp.text
        if (
            resp.status_code == 409
            or "DuplicateEntryError" in body
            or "already has a log with the same timestamp" in body
        ):
            return "duplicate", None
        if "No Employee found" in body:
            return "no_employee", None
        return "error", f"HTTP_{resp.status_code}:{_truncate_reason(body, 100)}"
    except requests.exceptions.RequestException as ex:
        return "error", f"network:{ex}"
    except Exception as ex:
        return "error", str(ex)


def _fetch_unpushed(engine: Engine, limit: int) -> list[dict]:
    stmt = (
        select(
            attendance_logs.c.id,
            attendance_logs.c.user_id,
            attendance_logs.c.timestamp,
            attendance_logs.c.device_id,
            attendance_logs.c.log_type,
        )
        .where(attendance_logs.c.pushed_to_erp == ERP_PUSH_PENDING)
        .order_by(attendance_logs.c.timestamp.asc())
        .limit(limit)
    )
    with engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
        return [dict(r) for r in rows]


def run() -> None:
    log.info("CFG   ERP_PUSH_SERVICE ERPNext=%s MySQL=%s", ERPNEXT_URL, MYSQL_URL.split("@")[-1])
    log.info(
        "CFG   batch_size=%d interval_sec=%d mode=%s pushed_to_erp: 0=pending 1=ok/dup 2=no_employee",
        ERP_PUSH_BATCH_SIZE,
        ERP_PUSH_INTERVAL,
        ERP_PUSH_MODE,
    )

    engine = create_engine(
        MYSQL_URL,
        pool_size=5,
        pool_pre_ping=True,
        pool_recycle=1800,
        future=True,
    )

    total_pushed = 0
    cycle = 0

    while True:
        cycle += 1
        records = _fetch_unpushed(engine, ERP_PUSH_BATCH_SIZE)

        if not records:
            log.info("CYCLE cycle=%d pending=0 sleep_sec=%d", cycle, ERP_PUSH_INTERVAL)
            time.sleep(ERP_PUSH_INTERVAL)
            continue

        ok_ids: list[int] = []
        dup_ids: list[int] = []
        skip_ids: list[int] = []
        err_count = 0

        if ERP_PUSH_MODE == "bulk":
            ok_ids, dup_ids, skip_ids, err_count = _push_bulk(records)
        else:
            for rec in records:
                result, fail_detail = _push_one(rec)
                if result == "ok":
                    ok_ids.append(rec["id"])
                    _erp_log_ok(rec, "ok")
                elif result == "duplicate":
                    dup_ids.append(rec["id"])
                    _erp_log_ok(rec, "duplicate")
                elif result == "no_employee":
                    skip_ids.append(rec["id"])
                    _erp_log_skip(rec, "no_employee")
                else:
                    err_count += 1
                    _erp_log_fail(rec, fail_detail or "error")

        mark_pushed_to_erp(engine, ok_ids + dup_ids)
        mark_erp_no_employee(engine, skip_ids)
        total_pushed += len(ok_ids)
        skip_count = len(skip_ids)

        log.info(
            "CYCLE cycle=%d ok=%d dup=%d skip=%d err=%d total_new_inserts=%d sleep_sec=%d",
            cycle,
            len(ok_ids),
            len(dup_ids),
            skip_count,
            err_count,
            total_pushed,
            ERP_PUSH_INTERVAL,
        )
        time.sleep(ERP_PUSH_INTERVAL)


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        log.info("Stopped by user.")