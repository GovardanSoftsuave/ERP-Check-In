from __future__ import annotations

import logging
from datetime import datetime

import win32com.client

from .config import INOUT_MAP, VERIFY_MAP, DeviceConfig
from .models import AttendanceLog

log = logging.getLogger(__name__)

_TIME_FMT = "%Y-%m-%d %H:%M:%S"


class ZkSdkClient:
    """
    Wrapper over the ZKEM COM SDK (zkemkeeper.ZKEM).

    Preferred method:
      ReadTimeGLogData(dwMachineNumber, sTime, eTime)
        - sTime/eTime are BSTR strings "YYYY-MM-DD hh:mm:ss"
        - Only downloads records within the time window (faster)
        - Available on "new architecture" firmware

    Fallback (proven in production zk_sync):
      ReadGeneralLogData(dwMachineNumber)
        - Downloads ALL records, we filter in Python
        - Works on all firmware versions

    After either call, records are iterated with SSR_GetGeneralLogData.
    """

    def __init__(self, dev: DeviceConfig):
        self.dev = dev
        self.zk = win32com.client.Dispatch("zkemkeeper.ZKEM")

    def connect(self) -> bool:
        self.zk.SetCommPasswordEx(self.dev.password)
        ok = self.zk.Connect_Net(self.dev.ip, int(self.dev.port))
        if ok:
            log.info("Connected to %s (%s:%d)", self.dev.label or self.dev.ip, self.dev.ip, self.dev.port)
        else:
            err = self._last_error()
            log.error("FAILED to connect %s (%s:%d) ErrorCode=%s", self.dev.label or self.dev.ip, self.dev.ip, self.dev.port, err)
        return bool(ok)

    def disconnect(self) -> None:
        try:
            self.zk.Disconnect()
        except Exception:
            pass

    def _last_error(self) -> str:
        try:
            ret = self.zk.GetLastError(0)
            return str(ret[1]) if isinstance(ret, tuple) else str(ret)
        except Exception:
            return "unknown"

    def read_time_range(self, start_time: datetime, end_time: datetime) -> list[AttendanceLog]:
        machine_id = int(self.dev.machine_id)
        tag = self.dev.label or self.dev.ip
        self.zk.EnableDevice(machine_id, False)
        try:
            used_time_filter = self._try_read_time_g_log(machine_id, start_time, end_time, tag)
            if used_time_filter is None:
                used_time_filter = False
                ok = bool(self.zk.ReadGeneralLogData(machine_id))
                if not ok:
                    err = self._last_error()
                    log.warning("[%s] ReadGeneralLogData returned False (ErrorCode=%s)", tag, err)
                    return []

            return self._iterate_logs(machine_id, start_time, end_time, skip_filter=used_time_filter)
        finally:
            self.zk.EnableDevice(machine_id, True)

    def _try_read_time_g_log(self, machine_id: int, start_time: datetime, end_time: datetime, tag: str) -> bool | None:
        s_time = start_time.strftime(_TIME_FMT)
        e_time = end_time.strftime(_TIME_FMT)
        try:
            ok = bool(self.zk.ReadTimeGLogData(machine_id, s_time, e_time))
            if ok:
                log.debug("[%s] ReadTimeGLogData OK  range=[%s .. %s]", tag, s_time, e_time)
                return True
            else:
                log.warning("[%s] ReadTimeGLogData returned False (ErrorCode=%s), falling back", tag, self._last_error())
                return None
        except Exception as ex:
            log.info("[%s] ReadTimeGLogData not supported (%s), using ReadGeneralLogData", tag, ex)
            return None

    def _iterate_logs(
        self,
        machine_id: int,
        start_time: datetime,
        end_time: datetime,
        skip_filter: bool,
    ) -> list[AttendanceLog]:
        out: list[AttendanceLog] = []
        sdwEnrollNumber = ""
        idwVerifyMode = idwInOutMode = 0
        idwYear = idwMonth = idwDay = 0
        idwHour = idwMinute = idwSecond = 0
        idwWorkcode = 0

        while True:
            ret = self.zk.SSR_GetGeneralLogData(
                machine_id,
                sdwEnrollNumber,
                idwVerifyMode,
                idwInOutMode,
                idwYear,
                idwMonth,
                idwDay,
                idwHour,
                idwMinute,
                idwSecond,
                idwWorkcode,
            )
            if not (isinstance(ret, tuple) and ret[0]):
                break

            (
                _,
                sdwEnrollNumber,
                idwVerifyMode,
                idwInOutMode,
                idwYear,
                idwMonth,
                idwDay,
                idwHour,
                idwMinute,
                idwSecond,
                idwWorkcode,
            ) = ret

            ts = datetime(
                int(idwYear),
                int(idwMonth),
                int(idwDay),
                int(idwHour),
                int(idwMinute),
                int(idwSecond),
            )

            if not skip_filter and (ts < start_time or ts > end_time):
                continue

            out.append(
                AttendanceLog(
                    source_ip=self.dev.ip,
                    user_id=str(sdwEnrollNumber).strip(),
                    timestamp=ts,
                    log_type=self.dev.log_type,
                    check_type=INOUT_MAP.get(int(idwInOutMode), f"MODE={idwInOutMode}"),
                    verify_mode=VERIFY_MAP.get(int(idwVerifyMode), str(idwVerifyMode)),
                    workcode=int(idwWorkcode),
                    device_id=self.dev.device_id,
                )
            )

        return out
