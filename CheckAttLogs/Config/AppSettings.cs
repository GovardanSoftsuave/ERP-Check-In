using System.Collections.Generic;
using System.IO;
using System.Web.Script.Serialization;

namespace CheckAttLogs.Config
{
    public class AppSettings
    {
        public DeviceConfig[] Devices { get; set; }
        public ErpNextConfig ErpNext { get; set; }
        public SyncConfig Sync { get; set; }
        public Dictionary<string, string> EmployeeIdMap { get; set; }
    }

    public class DeviceConfig
    {
        public string IP { get; set; }
        public int Port { get; set; }
        public int MachineNo { get; set; }
        public string Password { get; set; }
        public string LogType { get; set; }
        public string DeviceId { get; set; }
        public string Label { get; set; }
    }

    public class ErpNextConfig
    {
        public string Url { get; set; }
        public string ApiKey { get; set; }
        public string ApiSecret { get; set; }
    }

    public class SyncConfig
    {
        public int PollIntervalSeconds { get; set; }
        public string OutputCsv { get; set; }
        public string StateFile { get; set; }
        public string StartDate { get; set; }
    }

    public static class ConfigLoader
    {
        public static AppSettings Load(string path = "appsettings.json")
        {
            if (!File.Exists(path))
                throw new FileNotFoundException("Configuration file not found: " + path);

            string json = File.ReadAllText(path);
            var serializer = new JavaScriptSerializer();
            var settings = serializer.Deserialize<AppSettings>(json);

            // Defaults
            if (settings.Sync == null)
                settings.Sync = new SyncConfig();
            if (settings.Sync.PollIntervalSeconds < 5)
                settings.Sync.PollIntervalSeconds = 60;
            if (string.IsNullOrEmpty(settings.Sync.OutputCsv))
                settings.Sync.OutputCsv = "attlogs.csv";
            if (string.IsNullOrEmpty(settings.Sync.StateFile))
                settings.Sync.StateFile = "sync_state.json";
            if (settings.EmployeeIdMap == null)
                settings.EmployeeIdMap = new Dictionary<string, string>();
            if (settings.Devices == null)
                settings.Devices = new DeviceConfig[0];

            return settings;
        }
    }
}
