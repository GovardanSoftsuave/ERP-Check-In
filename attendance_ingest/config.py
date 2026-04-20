"""
Hardcoded configuration. Edit values below directly — no env vars needed.
Mirrors the style of zk_sync/config.py.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

# ── Mode ─────────────────────────────────────────────────────────────
# True  = mock devices (simulated data, no hardware access)
# False = real biometric devices
TESTING_MODE = False

# ── First-Run Backfill ───────────────────────────────────────────────
# When a device has NO data in the DB yet, fetch logs from this date.
# After data exists, polling resumes from MAX(timestamp) in the DB.
START_DATE = "2026-04-09 00:00:00"

# ── Device Config (matches zk_sync/config.py DEVICES) ───────────────
DEVICES = [
    {
        "ip": "192.168.12.61",
        "port": 4370,
        "machine_id": 1,
        "password": "000000",
        "label": "OUT-Device(61)",
        "log_type": "OUT",
        "device_id": 9,
    },
    {
        "ip": "192.168.12.62",
        "port": 4370,
        "machine_id": 1,
        "password": "000000",
        "label": "IN-Device(62)",
        "log_type": "IN",
        "device_id": 8,
    },
    {
        "ip": "192.168.12.63",
        "port": 4370,
        "machine_id": 1,
        "password": "000000",
        "label": "OUT-Device(63)",
        "log_type": "OUT",
        "device_id": 11,
    },
    {
        "ip": "192.168.12.64",
        "port": 4370,
        "machine_id": 1,
        "password": "000000",
        "label": "IN-Device(64)",
        "log_type": "IN",
        "device_id": 10,
    },
]

# Mock devices used when TESTING_MODE = True
MOCK_DEVICES = [
    {"ip": "mock-device-1", "port": 0, "machine_id": 1, "label": "MockDevice-1", "log_type": "IN", "device_id": 1},
    {"ip": "mock-device-2", "port": 0, "machine_id": 2, "label": "MockDevice-2", "log_type": "OUT", "device_id": 2},
]

# ── Redis Config ─────────────────────────────────────────────────────
REDIS_URL = "redis://localhost:6379/0"
REDIS_KEY_PREFIX = "test:"

# ── MySQL Config ─────────────────────────────────────────────────────
MYSQL_URL = "mysql+pymysql://root:root@localhost:3306/attendance_prod"

# ── Sync Config ──────────────────────────────────────────────────────
POLL_SECONDS = 60

# ── ERPNext Config ───────────────────────────────────────────────────
ERPNEXT_URL = "http://50.117.13.34"
ERPNEXT_API_KEY = "d31cc68b4834ad3"
ERPNEXT_API_SECRET = "e0d3cacfa2315bf"
ERP_SECRET_KEY = "928ccec7b82f2e7"
ERP_PUSH_INTERVAL = 60       # seconds (5 minutes)
ERP_PUSH_BATCH_SIZE = 50     # records per push cycle

ERP_PUSH_MODE = "bulk"  # "single" | "bulk"
ERP_BULK_URL = "http://50.117.13.34/api/method/ss_custom_erpnext.ss_custom_erpnext.api.bulk_create_checkins.bulk_create_checkins"

# Optional: only for exceptions (e.g. non-SS device ids). The Frappe bulk API should
# resolve numeric enroll → Employee.attendance_device_id (SS…) — see server_patches/bulk_create_checkins.py
EMPLOYEE_ID_MAP: dict[str, str] = {
    "21": "SS0021",
    "24": "SS0024",
    "01": "SS0001",
    "14": "SS0014",
    "12": "SS0012",
    "393": "SS00393",
    "656": "SS00656",
    "191": "SS00191",
    "300": "SS00300",
    "299": "SS00299",
    "106": "SS00106",
    "241": "SS00241",
    "493": "SS00493",
    "707": "SS00707",
    "664": "SS00664",
    "306": "SS00306",
    "720": "SS00720",
    "721": "SS00721",
    "490": "SS00490",
    "484": "SS00484",
    "375": "SS00375",
    "195": "SS00195",
    "751": "SS00751",
    "756": "SS00756",
}

# ── Lookup Maps ──────────────────────────────────────────────────────

INOUT_MAP: dict[int, str] = {
    0: "CHECK-IN",
    1: "CHECK-OUT",
    2: "BREAK-OUT",
    3: "BREAK-IN",
    4: "OVERTIME-IN",
    5: "OVERTIME-OUT",
}

VERIFY_MAP: dict[int, str] = {
    0: "Password",
    1: "Fingerprint",
    2: "RFID Card",
    3: "Password",
    4: "Card+FP",
    10: "Face",
    15: "Face+FP",
}


# ── Dataclasses (used by the rest of the codebase) ───────────────────

@dataclass(frozen=True)
class DeviceConfig:
    ip: str
    port: int = 4370
    machine_id: int = 1
    password: str = "000000"
    label: str = ""
    log_type: str = ""
    device_id: int = 0


@dataclass(frozen=True)
class Settings:
    testing_mode: bool
    poll_seconds: int
    redis_url: str
    redis_key_prefix: str
    mysql_url: str
    devices: list[DeviceConfig]


def load_settings() -> Settings:
    testing_mode = TESTING_MODE

    if testing_mode:
        log.warning(
            "*** TESTING MODE ENABLED ***  "
            "Device workers will generate simulated data.  "
            "Set TESTING_MODE = False in config.py when ready for production."
        )
        device_list = MOCK_DEVICES
    else:
        device_list = DEVICES

    devices = [DeviceConfig(**d) for d in device_list]

    return Settings(
        testing_mode=testing_mode,
        poll_seconds=max(POLL_SECONDS, 5),
        redis_url=REDIS_URL,
        redis_key_prefix=REDIS_KEY_PREFIX,
        mysql_url=MYSQL_URL,
        devices=devices,
    )