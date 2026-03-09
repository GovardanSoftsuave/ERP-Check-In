"""
Hardcoded configuration. Mirrors C# Config/AppSettings.cs + appsettings.json.
"""

# ── Device Config (matches C# DeviceConfig) ──────────────────────────
# Each device has its own StartDate, OutputCsv, and StateFile.

DEVICES = [
    {
        "IP": "192.168.12.61",
        "Port": 4370,
        "MachineNo": 1,
        "Password": "000000",
        "LogType": "OUT",
        "DeviceId": "9",
        "Label": "OUT-Device(61)",
        "StartDate": "2026-03-08 22:15:00",
        "OutputCsv": "attlogs_device9.csv",
        "StateFile": "sync_state_device9.txt",
    },
    {
        "IP": "192.168.12.62",
        "Port": 4370,
        "MachineNo": 1,
        "Password": "000000",
        "LogType": "IN",
        "DeviceId": "8",
        "Label": "IN-Device(62)",
        "StartDate": "2026-03-08 21:26:00",
        "OutputCsv": "attlogs_device8.csv",
        "StateFile": "sync_state_device8.txt",
    },
]

# ── ERPNext Config (matches C# ErpNextConfig) ────────────────────────

ERPNEXT_URL = "http://50.117.13.34"
ERPNEXT_API_KEY = "d31cc68b4834ad3"
ERPNEXT_API_SECRET = "e0d3cacfa2315bf"

# ── Sync Config (matches C# SyncConfig) ──────────────────────────────

POLL_SECONDS = 60

# ── Logging Config ───────────────────────────────────────────────────

import os as _os
_PKG_DIR = _os.path.dirname(_os.path.abspath(__file__))
LOG_DIR = _os.path.join(_PKG_DIR, "logs")

# ── Employee ID Map (device user ID -> ERPNext employee ID) ──────────

EMPLOYEE_ID_MAP = {
    "21": "SS0021",   "24": "SS0024",   "01": "SS0001",
    "14": "SS0014",   "12": "SS0012",   "393": "SS00393",
    "656": "SS00656", "191": "SS00191", "300": "SS00300",
    "299": "SS00299", "106": "SS00106", "241": "SS00241",
    "493": "SS00493", "707": "SS00707", "664": "SS00664",
    "306": "SS00306", "720": "SS00720", "721": "SS00721",
    "490": "SS00490", "484": "SS00484", "375": "SS00375",
    "195": "SS00195", "751": "SS00751", "756": "SS00756",
}

# ── Lookup Maps ──────────────────────────────────────────────────────

INOUT_MAP = {
    0: "CHECK-IN",
    1: "CHECK-OUT",
    2: "BREAK-OUT",
    3: "BREAK-IN",
    4: "OVERTIME-IN",
    5: "OVERTIME-OUT",
}

VERIFY_MAP = {
    0: "Password",
    1: "Fingerprint",
    2: "RFID Card",
    3: "Password",
    4: "Card+FP",
    10: "Face",
    15: "Face+FP",
}
