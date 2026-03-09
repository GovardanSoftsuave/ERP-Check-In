"""
Data models. Mirrors C# Models/AttRecord.cs + SyncEngine shared state.
"""

import threading


class AttRecord:
    """Single attendance log record. Mirrors C# AttRecord."""
    __slots__ = ("source_ip", "log_type", "device_id",
                 "user_id", "timestamp", "check_type", "verify_mode", "workcode")

    def __init__(self, source_ip, log_type, device_id,
                 user_id, timestamp, check_type, verify_mode, workcode):
        self.source_ip = source_ip
        self.log_type = log_type
        self.device_id = device_id
        self.user_id = user_id
        self.timestamp = timestamp
        self.check_type = check_type
        self.verify_mode = verify_mode
        self.workcode = workcode


class SharedState:
    """Thread-safe shared state. Mirrors C# SyncEngine._seenRecords + _lock."""

    def __init__(self):
        self.lock = threading.Lock()
        self.seen_records = set()       # sourceIP|userID|timestamp|checkType
        self.pushed_records = set()     # sourceIP|userID|timestamp
        self.unknown_users = set()
        self.running = True

    def add_seen(self, key):
        with self.lock:
            if key in self.seen_records:
                return False
            self.seen_records.add(key)
            return True

    def is_pushed(self, key):
        with self.lock:
            return key in self.pushed_records

    def mark_pushed(self, key):
        with self.lock:
            self.pushed_records.add(key)

    def is_unknown(self, user_id):
        with self.lock:
            return user_id in self.unknown_users

    def add_unknown(self, user_id):
        with self.lock:
            self.unknown_users.add(user_id)

    def get_unknowns(self):
        with self.lock:
            return set(self.unknown_users)

    def stop(self):
        self.running = False
