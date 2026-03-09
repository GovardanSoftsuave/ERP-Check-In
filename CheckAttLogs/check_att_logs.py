"""
ZK Device -> ERPNext Attendance Sync Service (Python + win32com/zkemkeeper COM SDK)

Connects to ZK biometric devices via the zkemkeeper COM SDK (same as the C# version),
pulls attendance logs, and pushes them to ERPNext Employee Checkin API.

Features:
  - Multi-threaded: one thread per device (matches C# SyncEngine)
  - Config loaded from appsettings.json
  - Periodic polling with configurable interval
  - Resume from where it left off (sync_state.txt)
  - CSV backup of all records
  - Auto-reconnect on connection loss

Prerequisites:
  pip install pywin32 requests
  Register zkemkeeper.dll: regsvr32 zkemkeeper.dll  (run as Administrator)

Usage:
  python check_att_logs.py                 (sync mode, default)
  python check_att_logs.py --poll 30       (custom interval)
  python check_att_logs.py --fetch-only    (fetch, no ERPNext)
  python check_att_logs.py --csv           (show CSV summary)
  python check_att_logs.py --test          (test connectivity only)
  python check_att_logs.py --config path   (custom config file)
"""

import argparse
import csv
import json
import os
import signal
import sys
import threading
import time
from datetime import datetime

import pythoncom
import requests
import win32com.client

# ─── Constants ───────────────────────────────────────────────────────

SEP = "-" * 110
SEP2 = "=" * 110

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


# ─── Config Loader ───────────────────────────────────────────────────

def load_config(path="appsettings.json"):
    """Load configuration from appsettings.json. Matches C# ConfigLoader.Load()."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"Configuration file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    if "Sync" not in cfg:
        cfg["Sync"] = {}
    sync = cfg["Sync"]
    if sync.get("PollIntervalSeconds", 0) < 5:
        sync["PollIntervalSeconds"] = 60
    if not sync.get("OutputCsv"):
        sync["OutputCsv"] = "attlogs.csv"
    if not sync.get("StateFile"):
        sync["StateFile"] = "sync_state.txt"
    if "EmployeeIdMap" not in cfg:
        cfg["EmployeeIdMap"] = {}
    if "Devices" not in cfg:
        cfg["Devices"] = []
    if "ErpNext" not in cfg:
        cfg["ErpNext"] = {"Url": "", "ApiKey": "", "ApiSecret": ""}

    return cfg


# ─── Data Model ──────────────────────────────────────────────────────

class AttRecord:
    def __init__(self, source_ip, fixed_log_type, fixed_device_id,
                 user_id, timestamp, check_type, verify_mode, workcode):
        self.source_ip = source_ip
        self.fixed_log_type = fixed_log_type
        self.fixed_device_id = fixed_device_id
        self.user_id = user_id
        self.timestamp = timestamp
        self.check_type = check_type
        self.verify_mode = verify_mode
        self.workcode = workcode


# ─── Thread-Safe Shared State ────────────────────────────────────────

class SharedState:
    """Thread-safe shared state. Matches C# SyncEngine._seenRecords + _lock."""

    def __init__(self):
        self.lock = threading.Lock()
        self.seen_records = set()
        self.pushed_records = set()
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

    def add_unknown_user(self, user_id):
        with self.lock:
            self.unknown_users.add(user_id)

    def is_unknown_user(self, user_id):
        with self.lock:
            return user_id in self.unknown_users

    def get_unknown_users(self):
        with self.lock:
            return set(self.unknown_users)

    def stop(self):
        self.running = False


# ─── Helpers ─────────────────────────────────────────────────────────

def print_header(title):
    print(f"\n{SEP2}")
    print(f"  {title}")
    print(SEP2)


def print_col_header():
    print(SEP)
    print(f"  {'SOURCE':<16} {'USER ID':<10} {'TIMESTAMP':<22} {'LOG TYPE':<10} {'VERIFY':<12} WORKCODE")
    print(SEP)


def print_row(source, user_id, timestamp, log_type, verify, workcode):
    print(f"  {source:<16} {user_id:<10} {timestamp:<22} {log_type:<10} {verify:<12} {workcode}")


def wait_seconds(seconds, state):
    """Interruptible sleep. Matches C# SyncEngine.WaitSeconds()."""
    for _ in range(seconds):
        if not state.running:
            break
        time.sleep(1)


# ─── Sync State Persistence ─────────────────────────────────────────

def load_sync_state(state, state_file):
    if not os.path.exists(state_file):
        return
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    state.pushed_records.add(line)
    except Exception as ex:
        print(f"  WARN: Could not load state file: {ex}")


def save_sync_state(state, state_file):
    try:
        with open(state_file, "w", encoding="utf-8") as f:
            for key in state.pushed_records:
                f.write(key + "\n")
    except Exception as ex:
        print(f"  WARN: Could not save state file: {ex}")


# ─── CSV ─────────────────────────────────────────────────────────────

def save_to_csv(records, csv_path):
    file_exists = os.path.exists(csv_path) and os.path.getsize(csv_path) > 0
    with open(csv_path, "a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["source_ip", "user_id", "timestamp", "log_type",
                             "check_type", "verify_mode", "workcode", "device_id"])
        for r in records:
            writer.writerow([r.source_ip, r.user_id, r.timestamp, r.fixed_log_type,
                             r.check_type, r.verify_mode, r.workcode, r.fixed_device_id])
    print(f"  CSV: saved {len(records)} records to {csv_path}")


def load_seen_from_csv(state, csv_path):
    if not os.path.exists(csv_path):
        return
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader, None)  # skip header
        for parts in reader:
            if len(parts) >= 5:
                key = f"{parts[0]}|{parts[1]}|{parts[2]}|{parts[4]}"
                state.seen_records.add(key)


# ─── Device Connection (win32com + zkemkeeper COM SDK) ───────────────

def connect_device(dev_cfg):
    """Connect to a ZK device via COM SDK. Returns COM object or None.
    Matches C# ZkDeviceService.Connect()."""
    try:
        zk = win32com.client.Dispatch("zkemkeeper.ZKEM")
    except Exception as e:
        print(f"FAILED (COM error: {e})")
        return None

    password = dev_cfg.get("Password", "000000")
    zk.SetCommPasswordEx(password)

    ok = zk.Connect_Net(dev_cfg["IP"], dev_cfg["Port"])
    if ok:
        print("OK")
        return zk
    else:
        ret = zk.GetLastError(0)
        err_code = ret[1] if isinstance(ret, tuple) else ret
        print(f"FAILED (ErrorCode={err_code})")
        return None


def read_logs_from_device(zk, dev_cfg, start_date=None):
    """Read attendance logs via COM SDK.
    Matches C# ZkDeviceService.ReadLogs() and Python check_logs.py."""
    records = []
    machine_no = dev_cfg.get("MachineNo", 1)

    zk.EnableDevice(machine_no, False)

    try:
        ok = zk.ReadGeneralLogData(machine_no)
        if not ok:
            ret = zk.GetLastError(0)
            err_code = ret[1] if isinstance(ret, tuple) else ret
            print(f"  {dev_cfg['Label']}: ReadGeneralLogData failed (ErrorCode={err_code})")
            return records

        sdwEnrollNumber = ""
        idwVerifyMode = 0
        idwInOutMode = 0
        idwYear = idwMonth = idwDay = 0
        idwHour = idwMinute = idwSecond = 0
        idwWorkcode = 0

        while True:
            ret = zk.SSR_GetGeneralLogData(
                machine_no,
                sdwEnrollNumber, idwVerifyMode, idwInOutMode,
                idwYear, idwMonth, idwDay,
                idwHour, idwMinute, idwSecond,
                idwWorkcode
            )

            if isinstance(ret, tuple):
                success = ret[0]
                if not success:
                    break
                (_, sdwEnrollNumber, idwVerifyMode, idwInOutMode,
                 idwYear, idwMonth, idwDay,
                 idwHour, idwMinute, idwSecond, idwWorkcode) = ret
            else:
                if not ret:
                    break

            ts = (f"{idwYear:04d}-{idwMonth:02d}-{idwDay:02d} "
                  f"{idwHour:02d}:{idwMinute:02d}:{idwSecond:02d}")

            if start_date and ts < start_date:
                continue

            check_type = INOUT_MAP.get(idwInOutMode, f"MODE={idwInOutMode}")
            verify_mode = VERIFY_MAP.get(idwVerifyMode, str(idwVerifyMode))

            records.append(AttRecord(
                source_ip=dev_cfg["IP"],
                fixed_log_type=dev_cfg["LogType"],
                fixed_device_id=dev_cfg["DeviceId"],
                user_id=str(sdwEnrollNumber).strip(),
                timestamp=ts,
                check_type=check_type,
                verify_mode=verify_mode,
                workcode=str(idwWorkcode),
            ))
    except Exception as ex:
        print(f"  {dev_cfg['Label']}: Error reading logs: {ex}")
    finally:
        zk.EnableDevice(machine_no, True)

    return records


# ─── ERPNext API ─────────────────────────────────────────────────────

def push_to_erpnext(record, config, state):
    """Push a single record to ERPNext. Returns True on success/duplicate.
    Matches C# ErpNextService.Push()."""
    if state.is_unknown_user(record.user_id):
        return False

    employee_id_map = config["EmployeeIdMap"]
    erpnext = config["ErpNext"]

    employee_field_value = employee_id_map.get(record.user_id, record.user_id)

    url = (erpnext["Url"].rstrip("/") +
           "/api/method/hrms.hr.doctype.employee_checkin.employee_checkin.add_log_based_on_employee_field")

    payload = {
        "employee_field_value": employee_field_value,
        "timestamp": record.timestamp,
        "device_id": record.fixed_device_id,
        "log_type": record.fixed_log_type,
    }

    headers = {
        "Authorization": f"token {erpnext['ApiKey']}:{erpnext['ApiSecret']}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        if resp.status_code in (200, 201):
            return True
        err_body = resp.text
        if resp.status_code == 409 or "DuplicateEntryError" in err_body or \
                "already has a log with the same timestamp" in err_body:
            return True
        if "No Employee found" in err_body:
            state.add_unknown_user(record.user_id)
            print(f"SKIP (no employee for ID={record.user_id})")
            return False
        print(f"ERR({resp.status_code}): {err_body[:150]}")
        return False
    except requests.exceptions.RequestException as ex:
        print(f"NET_ERR: {ex}")
        return False
    except Exception as ex:
        print(f"ERR: {ex}")
        return False


# ─── Test Connectivity ───────────────────────────────────────────────

def test_connectivity(config):
    print_header("CONNECTIVITY TEST")
    for dev_cfg in config["Devices"]:
        print(f"  Connecting to {dev_cfg['Label']} ({dev_cfg['IP']}:{dev_cfg['Port']}) ... ",
              end="", flush=True)
        zk = connect_device(dev_cfg)
        if zk:
            try:
                machine_no = dev_cfg.get("MachineNo", 1)
                zk.EnableDevice(machine_no, False)
                ok = zk.ReadGeneralLogData(machine_no)
                if ok:
                    count = 0
                    sdwEnrollNumber = ""
                    idwVerifyMode = idwInOutMode = 0
                    idwYear = idwMonth = idwDay = 0
                    idwHour = idwMinute = idwSecond = 0
                    idwWorkcode = 0
                    while True:
                        ret = zk.SSR_GetGeneralLogData(
                            machine_no,
                            sdwEnrollNumber, idwVerifyMode, idwInOutMode,
                            idwYear, idwMonth, idwDay,
                            idwHour, idwMinute, idwSecond,
                            idwWorkcode
                        )
                        if isinstance(ret, tuple) and ret[0]:
                            count += 1
                            (_, sdwEnrollNumber, idwVerifyMode, idwInOutMode,
                             idwYear, idwMonth, idwDay,
                             idwHour, idwMinute, idwSecond, idwWorkcode) = ret
                        else:
                            break
                    print(f"    Att Logs  : {count}")
                else:
                    ret = zk.GetLastError(0)
                    err_code = ret[1] if isinstance(ret, tuple) else ret
                    print(f"    ReadGeneralLogData failed (ErrorCode={err_code})")
                zk.EnableDevice(machine_no, True)
            except Exception as ex:
                print(f"    Info error: {ex}")
            finally:
                zk.Disconnect()
        else:
            print()


# ─── Fetch-Only Mode ────────────────────────────────────────────────

def fetch_and_save_to_csv(config):
    """Matches C# SyncEngine.FetchOnly()."""
    devices = config["Devices"]
    print_header(f"FETCH ONLY - ALL RECORDS  [{len(devices)} device(s)]")
    all_records = []

    for dev_cfg in devices:
        print(f"  Connecting to {dev_cfg['Label']} ({dev_cfg['IP']}:{dev_cfg['Port']}) ... ",
              end="", flush=True)
        zk = connect_device(dev_cfg)
        if not zk:
            continue
        try:
            start_date = config["Sync"].get("StartDate")
            records = read_logs_from_device(zk, dev_cfg, start_date)
            print(f"  {dev_cfg['Label']}: {len(records)} records.")
            all_records.extend(records)
        finally:
            zk.Disconnect()

    if not all_records:
        print("\n  No records found.")
        return

    print_col_header()
    for r in all_records:
        print_row(r.source_ip, r.user_id, r.timestamp, r.fixed_log_type,
                  r.verify_mode, r.workcode)
    print(SEP)

    users = set(r.user_id for r in all_records)
    print(f"\n  Total records : {len(all_records)}")
    print(f"  Unique users  : {len(users)}")

    save_to_csv(all_records, config["Sync"]["OutputCsv"])


# ─── CSV Summary ─────────────────────────────────────────────────────

def show_csv_summary(config, state):
    csv_path = config["Sync"]["OutputCsv"]
    state_file = config["Sync"]["StateFile"]

    print_header(f"SAVED CSV SUMMARY - {csv_path}")
    if not os.path.exists(csv_path):
        print(f"  File not found: {csv_path}")
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

    print(f"  Total records : {total}  (IN: {in_count}, OUT: {out_count})")
    print(f"  Unique users  : {len(users)}")
    print(f"  Earliest log  : {earliest or 'N/A'}")
    print(f"  Latest log    : {latest or 'N/A'}")

    load_sync_state(state, state_file)
    print(f"  Synced to ERPNext : {len(state.pushed_records)}")
    print(f"  Pending sync      : {total - len(state.pushed_records)}")


# ─── Multi-Threaded Sync Mode ───────────────────────────────────────

def run_sync_mode(config, state, poll_seconds):
    """Multi-threaded sync. One thread per device.
    Matches C# SyncEngine.RunSync()."""
    devices = config["Devices"]
    csv_path = config["Sync"]["OutputCsv"]
    state_file = config["Sync"]["StateFile"]

    print_header(f"ZK -> ERPNEXT SYNC SERVICE  (every {poll_seconds}s)  "
                 f"[{len(devices)} device(s), threaded]")
    for d in devices:
        print(f"  {d['Label']}  {d['IP']}:{d['Port']}  "
              f"LogType={d['LogType']}  DeviceId={d['DeviceId']}")
    print(f"  ERPNext   : {config['ErpNext']['Url']}")
    print(f"  State     : {state_file}")
    print(f"  StartDate : {config['Sync'].get('StartDate', '(all)')}")
    print(f"  ID Map    : {len(config['EmployeeIdMap'])} entries")
    print()

    load_sync_state(state, state_file)
    print(f"  Loaded {len(state.pushed_records)} previously synced records from state file.")

    load_seen_from_csv(state, csv_path)
    print(f"  Loaded {len(state.seen_records)} existing records from CSV.")

    print(f"  Each device runs on its own thread. Press Ctrl+C to stop.\n")

    def signal_handler(sig, frame):
        state.stop()
        print("\n  Stopping all threads...")

    signal.signal(signal.SIGINT, signal_handler)

    threads = []
    for dev_cfg in devices:
        t = threading.Thread(
            target=device_sync_loop,
            args=(dev_cfg, config, state, poll_seconds),
            name=dev_cfg["Label"],
            daemon=True,
        )
        threads.append(t)
        t.start()
        print(f"  Thread started: {dev_cfg['Label']}")

    for t in threads:
        t.join()

    print("  Disconnecting...")
    with state.lock:
        save_sync_state(state, state_file)
    print(f"  Done. Total synced records: {len(state.pushed_records)}")


def device_sync_loop(dev_cfg, config, state, poll_seconds):
    """Per-device sync loop running on its own thread.
    Matches C# SyncEngine.DeviceSyncLoop().

    CRITICAL: Must call pythoncom.CoInitialize() because COM objects
    are apartment-threaded -- each thread needs its own COM apartment.
    """
    pythoncom.CoInitialize()
    try:
        _device_sync_loop_inner(dev_cfg, config, state, poll_seconds)
    finally:
        pythoncom.CoUninitialize()


def _device_sync_loop_inner(dev_cfg, config, state, poll_seconds):
    tag = f"[{dev_cfg['Label']}]"
    start_date = config["Sync"].get("StartDate")
    employee_id_map = config["EmployeeIdMap"]
    csv_path = config["Sync"]["OutputCsv"]
    state_file = config["Sync"]["StateFile"]

    cycle = 0
    total_pushed = 0
    total_errors = 0
    zk = None
    connected = False

    # Initial connection
    print(f"  {tag} Connecting... ", end="", flush=True)
    zk = connect_device(dev_cfg)
    connected = (zk is not None)

    while state.running:
        cycle += 1
        now = datetime.now().strftime("%H:%M:%S")
        print(f"{tag} [{now}] Cycle #{cycle}")

        # Reconnect if needed
        if not connected:
            print(f"  {tag} Reconnecting... ", end="", flush=True)
            zk = connect_device(dev_cfg)
            connected = (zk is not None)
            if not connected:
                print(f"  {tag} Not connected, retrying in {poll_seconds}s...")
                wait_seconds(poll_seconds, state)
                continue

        try:
            # 1. Read logs from this device
            all_records = read_logs_from_device(zk, dev_cfg, start_date)
            print(f"  {tag} {len(all_records)} records read from device.")

            # 2. Filter new records (thread-safe)
            new_records = []
            for r in all_records:
                key = f"{r.source_ip}|{r.user_id}|{r.timestamp}|{r.check_type}"
                if state.add_seen(key):
                    new_records.append(r)

            if new_records:
                print(f"  {tag} {len(new_records)} new record(s).")
                with state.lock:
                    save_to_csv(new_records, csv_path)

            # 3. Find unpushed records
            to_push = []
            for r in all_records:
                push_key = f"{r.source_ip}|{r.user_id}|{r.timestamp}"
                if not state.is_pushed(push_key):
                    to_push.append(r)

            if not to_push:
                print(f"  {tag} All records synced.")
                wait_seconds(poll_seconds, state)
                continue

            # 4. Push to ERPNext
            print(f"  {tag} Pushing {len(to_push)} record(s) to ERPNext...")
            pushed = 0
            errors = 0
            skipped = 0

            for r in to_push:
                if not state.running:
                    break

                push_key = f"{r.source_ip}|{r.user_id}|{r.timestamp}"

                if state.is_unknown_user(r.user_id):
                    skipped += 1
                    continue

                mapped_id = employee_id_map.get(r.user_id, r.user_id)
                print(f"  {tag} [{pushed + errors + 1}/{len(to_push) - skipped}] "
                      f"{r.fixed_log_type} User={r.user_id}->{mapped_id}  "
                      f"Time={r.timestamp}  DevId={r.fixed_device_id} ... ",
                      end="", flush=True)

                ok = push_to_erpnext(r, config, state)
                if ok:
                    state.mark_pushed(push_key)
                    pushed += 1
                    print("OK")
                else:
                    errors += 1
                    if not state.is_unknown_user(r.user_id):
                        print("FAILED")

                time.sleep(1)

            # Save state after batch
            with state.lock:
                save_sync_state(state, state_file)

            total_pushed += pushed
            total_errors += errors
            print(f"  {tag} Pushed: {pushed}  Errors: {errors}  "
                  f"Skipped: {skipped}  (Total synced: {len(state.pushed_records)})")

            unknowns = state.get_unknown_users()
            if unknowns:
                print(f"  {tag} Unknown user IDs: {', '.join(unknowns)}")

        except Exception as ex:
            print(f"  {tag} Error: {ex}")
            if zk:
                try:
                    zk.Disconnect()
                except Exception:
                    pass
            zk = None
            connected = False

        wait_seconds(poll_seconds, state)

    # Thread stopping -- disconnect
    if zk and connected:
        try:
            zk.Disconnect()
        except Exception:
            pass

    print(f"  {tag} Thread stopped. Session pushed: {total_pushed}, errors: {total_errors}")


# ─── Main ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ZK Device -> ERPNext Attendance Sync (COM SDK)")
    parser.add_argument("--csv", action="store_true", help="Show CSV summary")
    parser.add_argument("--fetch-only", action="store_true", help="Fetch logs, no ERPNext push")
    parser.add_argument("--test", action="store_true", help="Test device connectivity only")
    parser.add_argument("--poll", type=int, default=None, help="Poll interval in seconds")
    parser.add_argument("--url", type=str, help="ERPNext URL override")
    parser.add_argument("--key", type=str, help="ERPNext API key override")
    parser.add_argument("--secret", type=str, help="ERPNext API secret override")
    parser.add_argument("--config", type=str, default="appsettings.json",
                        help="Path to config file (default: appsettings.json)")
    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except FileNotFoundError as e:
        print(f"FATAL: {e}")
        sys.exit(1)

    if args.url:
        config["ErpNext"]["Url"] = args.url
    if args.key:
        config["ErpNext"]["ApiKey"] = args.key
    if args.secret:
        config["ErpNext"]["ApiSecret"] = args.secret

    poll_seconds = args.poll or config["Sync"]["PollIntervalSeconds"]
    poll_seconds = max(poll_seconds, 5)

    state = SharedState()

    if args.csv:
        show_csv_summary(config, state)
    elif args.fetch_only:
        fetch_and_save_to_csv(config)
    elif args.test:
        test_connectivity(config)
    else:
        run_sync_mode(config, state, poll_seconds)


if __name__ == "__main__":
    main()
