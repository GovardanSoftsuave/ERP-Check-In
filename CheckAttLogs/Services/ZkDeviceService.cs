using System;
using System.Collections.Generic;
using CheckAttLogs.Config;
using CheckAttLogs.Interfaces;
using CheckAttLogs.Models;

namespace CheckAttLogs.Services
{
    public class ZkDeviceService : IDeviceService
    {
        private readonly Dictionary<string, zkemkeeper.CZKEMClass> _connections
            = new Dictionary<string, zkemkeeper.CZKEMClass>();
        private readonly Dictionary<string, bool> _connected
            = new Dictionary<string, bool>();

        private static readonly Dictionary<int, string> InOutMap = new Dictionary<int, string>
        {
            { 0, "CHECK-IN"     },
            { 1, "CHECK-OUT"    },
            { 2, "BREAK-OUT"    },
            { 3, "BREAK-IN"     },
            { 4, "OVERTIME-IN"  },
            { 5, "OVERTIME-OUT" },
        };

        private static readonly Dictionary<int, string> VerifyMap = new Dictionary<int, string>
        {
            { 0,  "Password"    },
            { 1,  "Fingerprint" },
            { 2,  "RFID Card"   },
            { 3,  "Password"    },
            { 4,  "Card+FP"     },
            { 10, "Face"        },
            { 15, "Face+FP"     },
        };

        private zkemkeeper.CZKEMClass GetOrCreateZk(DeviceConfig device)
        {
            if (!_connections.ContainsKey(device.IP))
            {
                _connections[device.IP] = new zkemkeeper.CZKEMClass();
                _connected[device.IP] = false;
            }
            return _connections[device.IP];
        }

        public bool Connect(DeviceConfig device)
        {
            var zk = GetOrCreateZk(device);
            zk.SetCommPasswordEx(device.Password);

            Console.Write("  Connecting to {0} ({1}:{2}) ... ", device.Label, device.IP, device.Port);
            bool ok = zk.Connect_Net(device.IP, device.Port);
            _connected[device.IP] = ok;

            if (ok)
            {
                Console.WriteLine("OK");
            }
            else
            {
                int err = 0;
                zk.GetLastError(ref err);
                Console.WriteLine("FAILED (ErrorCode={0})", err);
            }

            return ok;
        }

        public void Disconnect(DeviceConfig device)
        {
            if (_connections.ContainsKey(device.IP) && _connected.ContainsKey(device.IP) && _connected[device.IP])
            {
                _connections[device.IP].Disconnect();
                _connected[device.IP] = false;
            }
        }

        public void DisconnectAll()
        {
            foreach (var kvp in _connections)
            {
                if (_connected.ContainsKey(kvp.Key) && _connected[kvp.Key])
                {
                    kvp.Value.Disconnect();
                    _connected[kvp.Key] = false;
                }
            }
        }

        public bool IsConnected(DeviceConfig device)
        {
            return _connected.ContainsKey(device.IP) && _connected[device.IP];
        }

        public bool TryReconnect(DeviceConfig device)
        {
            if (IsConnected(device))
                return true;

            var zk = GetOrCreateZk(device);
            zk.SetCommPasswordEx(device.Password);
            bool ok = zk.Connect_Net(device.IP, device.Port);
            _connected[device.IP] = ok;

            if (ok)
                Console.WriteLine("  {0}: reconnected.", device.Label);

            return ok;
        }

        public List<AttRecord> ReadLogs(DeviceConfig device, string startDate)
        {
            var records = new List<AttRecord>();

            if (!IsConnected(device))
                return records;

            var zk = _connections[device.IP];
            zk.EnableDevice(device.MachineNo, false);

            try
            {
                if (!zk.ReadGeneralLogData(device.MachineNo))
                {
                    int err = 0;
                    zk.GetLastError(ref err);
                    Console.WriteLine("  {0}: ReadGeneralLogData failed (ErrorCode={1})", device.Label, err);
                    return records;
                }

                string sdwEnrollNumber = "";
                int idwVerifyMode = 0, idwInOutMode = 0;
                int idwYear = 0, idwMonth = 0, idwDay = 0;
                int idwHour = 0, idwMinute = 0, idwSecond = 0;
                int idwWorkcode = 0;

                while (zk.SSR_GetGeneralLogData(device.MachineNo,
                    out sdwEnrollNumber, out idwVerifyMode, out idwInOutMode,
                    out idwYear, out idwMonth, out idwDay,
                    out idwHour, out idwMinute, out idwSecond,
                    ref idwWorkcode))
                {
                    string ts = string.Format("{0:D4}-{1:D2}-{2:D2} {3:D2}:{4:D2}:{5:D2}",
                        idwYear, idwMonth, idwDay, idwHour, idwMinute, idwSecond);

                    // Skip records before start date
                    if (!string.IsNullOrEmpty(startDate) && string.Compare(ts, startDate) < 0)
                        continue;

                    string checkType;
                    if (!InOutMap.TryGetValue(idwInOutMode, out checkType))
                        checkType = "MODE=" + idwInOutMode;

                    string verifyMode;
                    if (!VerifyMap.TryGetValue(idwVerifyMode, out verifyMode))
                        verifyMode = idwVerifyMode.ToString();

                    records.Add(new AttRecord
                    {
                        SourceIP      = device.IP,
                        FixedLogType  = device.LogType,
                        FixedDeviceId = device.DeviceId,
                        UserID        = sdwEnrollNumber,
                        Timestamp     = ts,
                        CheckType     = checkType,
                        VerifyMode    = verifyMode,
                        Workcode      = idwWorkcode.ToString(),
                    });
                }
            }
            finally
            {
                zk.EnableDevice(device.MachineNo, true);
            }

            return records;
        }
    }
}
