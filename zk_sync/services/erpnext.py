"""
ERPNext push service. Mirrors C# Services/ErpNextService.cs.
"""

import logging

import requests

from ..config import ERPNEXT_URL, ERPNEXT_API_KEY, ERPNEXT_API_SECRET, EMPLOYEE_ID_MAP

log = logging.getLogger(__name__)


def push_to_erpnext(record, state):
    """Push one record to ERPNext.
    Returns 'ok', 'duplicate', 'no_employee', or 'error'.
    Mirrors C# ErpNextService.Push()."""
    if state.is_unknown(record.user_id):
        return "no_employee"

    mapped_id = EMPLOYEE_ID_MAP.get(record.user_id, record.user_id)

    url = (ERPNEXT_URL.rstrip("/") +
           "/api/method/hrms.hr.doctype.employee_checkin.employee_checkin"
           ".add_log_based_on_employee_field")

    payload = {
        "employee_field_value": mapped_id,
        "timestamp": record.timestamp,
        "device_id": record.device_id,
        "log_type": record.log_type,
    }
    headers = {
        "Authorization": f"token {ERPNEXT_API_KEY}:{ERPNEXT_API_SECRET}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        if resp.status_code in (200, 201):
            return "ok"
        body = resp.text
        if (resp.status_code == 409
                or "DuplicateEntryError" in body
                or "already has a log with the same timestamp" in body):
            return "duplicate"
        if "No Employee found" in body:
            state.add_unknown(record.user_id)
            log.warning("SKIP (no employee ID=%s)", record.user_id)
            return "no_employee"
        log.error("ERR(%d): %s", resp.status_code, body[:150])
        return "error"
    except requests.exceptions.RequestException as ex:
        log.error("NET_ERR: %s", ex)
        return "error"
    except Exception as ex:
        log.error("ERR: %s", ex)
        return "error"
