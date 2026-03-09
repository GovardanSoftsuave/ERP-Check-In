"""
Sync engine. Mirrors C# SyncEngine.cs.
Orchestrates device polling, ERPNext push, CSV save, and state persistence.
Each device has its own CSV file, state file, and start date.
"""

import logging
import signal
import threading
import time
from datetime import datetime

import pythoncom

from .config import DEVICES, ERPNEXT_URL, EMPLOYEE_ID_MAP, POLL_SECONDS
from .models import SharedState
from .services.device import connect_device, read_logs
from .services.erpnext import push_to_erpnext
from .services.state import load_state, save_state
from .services.csv_svc import save_csv, load_seen_from_csv, print_csv_summary

log = logging.getLogger(__name__)

SEP = "-" * 110
SEP2 = "=" * 110


def print_header(title):
    log.info(SEP2)
    log.info("  %s", title)
    log.info(SEP2)


def wait_seconds(seconds, state):
    """Interruptible sleep. Mirrors C# SyncEngine.WaitSeconds()."""
    for _ in range(seconds):
        if not state.running:
            break
        time.sleep(1)


# ═══════════════════════════════════════════════════════════════════════
#  SYNC MODE — MULTI-THREADED  (mirrors C# SyncEngine.RunSync)
# ═══════════════════════════════════════════════════════════════════════

def run_sync(poll_seconds):
    state = SharedState()

    print_header(f"ZK -> ERPNEXT SYNC SERVICE  (every {poll_seconds}s)  "
                 f"[{len(DEVICES)} device(s), threaded]")
    for d in DEVICES:
        log.info("  %s  %s:%d  LogType=%s  DeviceId=%s  StartDate=%s",
                 d["Label"], d["IP"], d["Port"], d["LogType"],
                 d["DeviceId"], d["StartDate"])
        log.info("    CSV=%s  State=%s", d["OutputCsv"], d["StateFile"])
    log.info("  ERPNext   : %s", ERPNEXT_URL)
    log.info("  ID Map    : %d entries", len(EMPLOYEE_ID_MAP))

    # Load persisted state from all device state files
    for d in DEVICES:
        load_state(state, d["StateFile"])
    log.info("Loaded %d previously synced records from state files.",
             len(state.pushed_records))

    # Load seen records from all device CSV files
    for d in DEVICES:
        load_seen_from_csv(state, d["OutputCsv"])
    log.info("Loaded %d existing records from CSV files.", len(state.seen_records))
    log.info("Each device runs on its own thread. Press Ctrl+C to stop.")

    # Graceful shutdown (mirrors C# Console.CancelKeyPress)
    def signal_handler(_sig, _frame):
        state.stop()
        log.info("Stopping all threads...")
    signal.signal(signal.SIGINT, signal_handler)

    # Start one thread per device (mirrors C# SyncEngine.cs:94-104)
    threads = []
    for dev in DEVICES:
        t = threading.Thread(
            target=device_sync_loop,
            args=(dev, state, poll_seconds),
            name=dev["Label"],
            daemon=True,
        )
        threads.append(t)
        t.start()
        log.info("Thread started: %s", dev["Label"])

    # Wait for all threads (mirrors C# SyncEngine.cs:107-108)
    for t in threads:
        t.join()

    # Clean shutdown — save state to each device's file
    log.info("Disconnecting...")
    with state.lock:
        for d in DEVICES:
            save_state(state, d["StateFile"])
    log.info("Done. Total synced records: %d", len(state.pushed_records))


def device_sync_loop(dev, state, poll_seconds):
    """Per-device sync loop. Mirrors C# SyncEngine.DeviceSyncLoop().
    Each thread needs its own COM apartment."""
    pythoncom.CoInitialize()
    try:
        _device_sync_inner(dev, state, poll_seconds)
    finally:
        pythoncom.CoUninitialize()


def _device_sync_inner(dev, state, poll_seconds):
    tag = f"[{dev['Label']}]"
    csv_path = dev["OutputCsv"]
    state_file = dev["StateFile"]
    start_date = dev["StartDate"]
    device_id = dev["DeviceId"]

    # Per-device push logger — only ERPNext push data goes to file
    push_log = logging.getLogger(f"push.device{device_id}")

    cycle = 0
    total_pushed = 0
    total_errors = 0
    zk = None
    connected = False

    # Initial connection
    log.info("%s Connecting...", tag)
    zk = connect_device(dev)
    connected = zk is not None

    while state.running:
        cycle += 1
        now = datetime.now().strftime("%H:%M:%S")
        log.info("%s [%s] Cycle #%d", tag, now, cycle)

        # Reconnect if needed (mirrors C# SyncEngine.cs:132-140)
        if not connected:
            log.info("%s Reconnecting...", tag)
            zk = connect_device(dev)
            connected = zk is not None
            if not connected:
                log.warning("%s Not connected, retrying in %ds...", tag, poll_seconds)
                wait_seconds(poll_seconds, state)
                continue

        try:
            # 1. Read logs using this device's start date
            all_records = read_logs(zk, dev, start_date)
            log.info("%s %d records read from device.", tag, len(all_records))

            # 2. Filter new records — thread-safe (mirrors C# SyncEngine.cs:147-156)
            new_records = []
            for r in all_records:
                key = f"{r.source_ip}|{r.user_id}|{r.timestamp}|{r.check_type}"
                if state.add_seen(key):
                    new_records.append(r)

            if new_records:
                log.info("%s %d new record(s).", tag, len(new_records))
                with state.lock:
                    save_csv(new_records, csv_path)

            # 3. Find unpushed records (mirrors C# SyncEngine.cs:165-174)
            to_push = []
            for r in all_records:
                push_key = f"{r.source_ip}|{r.user_id}|{r.timestamp}"
                if not state.is_pushed(push_key):
                    to_push.append(r)

            if not to_push:
                log.info("%s All records synced.", tag)
                wait_seconds(poll_seconds, state)
                continue

            # 4. Push to ERPNext (mirrors C# SyncEngine.cs:183-228)
            log.info("%s Pushing %d record(s) to ERPNext...", tag, len(to_push))
            pushed = 0
            errors = 0
            skipped = 0

            for r in to_push:
                if not state.running:
                    break

                push_key = f"{r.source_ip}|{r.user_id}|{r.timestamp}"

                if state.is_unknown(r.user_id):
                    skipped += 1
                    push_log.info("SKIP  User=%s  Time=%s  Reason=no_employee",
                                  r.user_id, r.timestamp)
                    continue

                mapped_id = EMPLOYEE_ID_MAP.get(r.user_id, r.user_id)
                result = push_to_erpnext(r, state)

                if result in ("ok", "duplicate"):
                    state.mark_pushed(push_key)
                    pushed += 1
                    push_log.info("OK    User=%s  MappedId=%s  Time=%s  "
                                  "LogType=%s  DevId=%s  Result=%s",
                                  r.user_id, mapped_id, r.timestamp,
                                  r.log_type, r.device_id, result)
                    log.info("%s [%d/%d] User=%s->%s  Time=%s -> OK",
                             tag, pushed + errors, len(to_push) - skipped,
                             r.user_id, mapped_id, r.timestamp)
                elif result == "no_employee":
                    skipped += 1
                    push_log.info("SKIP  User=%s  MappedId=%s  Time=%s  "
                                  "Reason=no_employee",
                                  r.user_id, mapped_id, r.timestamp)
                else:
                    errors += 1
                    push_log.error("FAIL  User=%s  MappedId=%s  Time=%s  "
                                   "LogType=%s  DevId=%s  Result=%s",
                                   r.user_id, mapped_id, r.timestamp,
                                   r.log_type, r.device_id, result)
                    log.error("%s [%d/%d] User=%s->%s  Time=%s -> FAILED",
                              tag, pushed + errors, len(to_push) - skipped,
                              r.user_id, mapped_id, r.timestamp)

                time.sleep(1)

            # Save state to this device's state file
            with state.lock:
                save_state(state, state_file)

            total_pushed += pushed
            total_errors += errors
            log.info("%s Pushed: %d  Errors: %d  Skipped: %d  (Total synced: %d)",
                     tag, pushed, errors, skipped, len(state.pushed_records))

            unknowns = state.get_unknowns()
            if unknowns:
                log.warning("%s Unknown user IDs: %s", tag, ", ".join(unknowns))

        except Exception as ex:
            log.error("%s Error: %s", tag, ex, exc_info=True)
            # Mark disconnected for reconnect next cycle
            if zk:
                try:
                    zk.Disconnect()
                except Exception:
                    pass
            zk = None
            connected = False

        wait_seconds(poll_seconds, state)

    # Thread stopping — disconnect
    if zk and connected:
        try:
            zk.Disconnect()
        except Exception:
            pass
    log.info("%s Thread stopped. Pushed: %d, errors: %d",
             tag, total_pushed, total_errors)


# ═══════════════════════════════════════════════════════════════════════
#  FETCH-ONLY MODE  (mirrors C# SyncEngine.FetchOnly)
# ═══════════════════════════════════════════════════════════════════════

def fetch_only():
    print_header(f"FETCH ONLY - ALL RECORDS  [{len(DEVICES)} device(s)]")

    for dev in DEVICES:
        log.info("Connecting to %s (%s:%d) ...", dev["Label"], dev["IP"], dev["Port"])
        zk = connect_device(dev)
        if not zk:
            continue
        try:
            records = read_logs(zk, dev, dev["StartDate"])
            log.info("%s: %d records.", dev["Label"], len(records))

            if not records:
                continue

            log.info(SEP)
            log.info("  %-16s %-10s %-22s %-10s %-12s %s",
                     "SOURCE", "USER ID", "TIMESTAMP", "LOG TYPE", "VERIFY", "WORKCODE")
            log.info(SEP)
            for r in records:
                log.info("  %-16s %-10s %-22s %-10s %-12s %s",
                         r.source_ip, r.user_id, r.timestamp,
                         r.log_type, r.verify_mode, r.workcode)
            log.info(SEP)

            users = set(r.user_id for r in records)
            log.info("  Total records : %d", len(records))
            log.info("  Unique users  : %d", len(users))

            # Save to this device's CSV file
            save_csv(records, dev["OutputCsv"])
        finally:
            zk.Disconnect()


# ═══════════════════════════════════════════════════════════════════════
#  TEST CONNECTIVITY
# ═══════════════════════════════════════════════════════════════════════

def test_connectivity():
    print_header("CONNECTIVITY TEST")
    for dev in DEVICES:
        log.info("Connecting to %s (%s:%d) ...", dev["Label"], dev["IP"], dev["Port"])
        zk = connect_device(dev)
        if not zk:
            continue
        try:
            machine_no = dev["MachineNo"]
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
                        idwWorkcode,
                    )
                    if isinstance(ret, tuple) and ret[0]:
                        count += 1
                        (_, sdwEnrollNumber, idwVerifyMode, idwInOutMode,
                         idwYear, idwMonth, idwDay,
                         idwHour, idwMinute, idwSecond, idwWorkcode) = ret
                    else:
                        break
                log.info("  %s: Att Logs = %d", dev["Label"], count)
            else:
                ret = zk.GetLastError(0)
                err_code = ret[1] if isinstance(ret, tuple) else ret
                log.error("  %s: ReadGeneralLogData FAILED (ErrorCode=%s)",
                          dev["Label"], err_code)
            zk.EnableDevice(machine_no, True)
        except Exception as ex:
            log.error("  %s: Error: %s", dev["Label"], ex)
        finally:
            zk.Disconnect()


# ═══════════════════════════════════════════════════════════════════════
#  CSV SUMMARY  (mirrors C# CsvService.PrintSummary)
# ═══════════════════════════════════════════════════════════════════════

def show_csv_summary():
    print_header("SAVED CSV SUMMARY")
    state = SharedState()
    for d in DEVICES:
        load_state(state, d["StateFile"])

    for d in DEVICES:
        log.info(SEP)
        log.info("  Device: %s  ->  %s", d["Label"], d["OutputCsv"])
        log.info(SEP)
        print_csv_summary(state, d["OutputCsv"])
