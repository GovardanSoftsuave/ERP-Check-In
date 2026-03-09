using System.Collections.Generic;
using CheckAttLogs.Config;
using CheckAttLogs.Models;

namespace CheckAttLogs.Interfaces
{
    public interface IDeviceService
    {
        bool Connect(DeviceConfig device);
        void Disconnect(DeviceConfig device);
        bool IsConnected(DeviceConfig device);
        bool TryReconnect(DeviceConfig device);
        List<AttRecord> ReadLogs(DeviceConfig device, string startDate);
        void DisconnectAll();
    }
}
