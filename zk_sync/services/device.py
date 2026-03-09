"""
ZK Device connection and log reading. Mirrors C# Services/ZkDeviceService.cs.
"""

import logging

import win32com.client

from ..config import INOUT_MAP, VERIFY_MAP
from ..models import AttRecord

log = logging.getLogger(__name__)


def connect_device(dev):
    """Connect via COM SDK. Returns COM object or None.
    Mirrors C# ZkDeviceService.Connect()."""
    try:
        zk = win32com.client.Dispatch("zkemkeeper.ZKEM")
    except Exception as e:
        log.error("FAILED (COM error: %s)", e)
        return None

    zk.SetCommPasswordEx(dev["Password"])
    ok = zk.Connect_Net(dev["IP"], dev["Port"])
    if ok:
        log.info("Connected to %s (%s:%d)", dev["Label"], dev["IP"], dev["Port"])
        return zk
    else:
        ret = zk.GetLastError(0)
        err_code = ret[1] if isinstance(ret, tuple) else ret
        log.error("FAILED to connect %s (ErrorCode=%s)", dev["Label"], err_code)
        return None


def read_logs(zk, dev, start_date=None):
    """Read attendance logs from device. Mirrors C# ZkDeviceService.ReadLogs()."""
    records = []
    machine_no = dev["MachineNo"]
    zk.EnableDevice(machine_no, False)
    try:
        ok = zk.ReadGeneralLogData(machine_no)
        if not ok:
            ret = zk.GetLastError(0)
            err_code = ret[1] if isinstance(ret, tuple) else ret
            log.error("%s: ReadGeneralLogData FAILED (ErrorCode=%s)",
                      dev["Label"], err_code)
            return records

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
                (_, sdwEnrollNumber, idwVerifyMode, idwInOutMode,
                 idwYear, idwMonth, idwDay,
                 idwHour, idwMinute, idwSecond, idwWorkcode) = ret
            else:
                break

            ts = (f"{idwYear:04d}-{idwMonth:02d}-{idwDay:02d} "
                  f"{idwHour:02d}:{idwMinute:02d}:{idwSecond:02d}")

            if start_date and ts < start_date:
                continue

            records.append(AttRecord(
                source_ip=dev["IP"],
                log_type=dev["LogType"],
                device_id=dev["DeviceId"],
                user_id=str(sdwEnrollNumber).strip(),
                timestamp=ts,
                check_type=INOUT_MAP.get(idwInOutMode, f"MODE={idwInOutMode}"),
                verify_mode=VERIFY_MAP.get(idwVerifyMode, str(idwVerifyMode)),
                workcode=str(idwWorkcode),
            ))
        log.debug("%s: read %d records (start_date=%s)",
                  dev["Label"], len(records), start_date)
    except Exception as ex:
        log.error("%s: Error reading logs: %s", dev["Label"], ex)
    finally:
        zk.EnableDevice(machine_no, True)
    return records
