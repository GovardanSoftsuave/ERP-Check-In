from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class AttendanceLog:
    source_ip: str
    user_id: str
    timestamp: datetime
    log_type: str        # "IN" / "OUT" (from device config)
    check_type: str      # "CHECK-IN", "CHECK-OUT", "MODE=255", etc.
    verify_mode: str     # "Fingerprint", "Face+FP", etc.
    workcode: int        # workcode from device, typically 0
    device_id: int       # device identifier from config, e.g. 8, 9

    def dedupe_key(self) -> tuple[str, str, datetime]:
        return (self.source_ip, self.user_id, self.timestamp)
