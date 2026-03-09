using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using CheckAttLogs.Interfaces;
using CheckAttLogs.Models;

namespace CheckAttLogs.Services
{
    public class CsvService : ICsvService
    {
        private readonly string _filePath;
        private const string HEADER = "source_ip,user_id,timestamp,log_type,check_type,verify_mode,workcode,device_id";

        public CsvService(string filePath)
        {
            _filePath = filePath;
        }

        public void Save(List<AttRecord> records)
        {
            bool fileExists = File.Exists(_filePath);

            using (var sw = new StreamWriter(_filePath, true, Encoding.UTF8))
            {
                if (!fileExists || new FileInfo(_filePath).Length == 0)
                    sw.WriteLine(HEADER);

                foreach (var r in records)
                {
                    sw.WriteLine("{0},{1},{2},{3},{4},{5},{6},{7}",
                        r.SourceIP, r.UserID, r.Timestamp, r.FixedLogType,
                        r.CheckType, r.VerifyMode, r.Workcode, r.FixedDeviceId);
                }
            }

            Console.WriteLine("  CSV: saved {0} records to {1}", records.Count, _filePath);
        }

        public HashSet<string> LoadSeenKeys()
        {
            var keys = new HashSet<string>();

            if (!File.Exists(_filePath))
                return keys;

            using (var sr = new StreamReader(_filePath))
            {
                sr.ReadLine(); // skip header
                string line;
                while ((line = sr.ReadLine()) != null)
                {
                    string[] parts = line.Split(',');
                    if (parts.Length >= 5)
                    {
                        // key: sourceIP|userID|timestamp|checkType
                        string key = parts[0] + "|" + parts[1] + "|" + parts[2] + "|" + parts[4];
                        keys.Add(key);
                    }
                }
            }

            return keys;
        }

        public void PrintSummary(ISyncStateService stateService)
        {
            if (!File.Exists(_filePath))
            {
                Console.WriteLine("  File not found: {0}", _filePath);
                return;
            }

            int total = 0;
            var users = new HashSet<string>();
            string earliest = null, latest = null;
            int inCount = 0, outCount = 0;

            using (var sr = new StreamReader(_filePath))
            {
                sr.ReadLine(); // skip header
                string line;
                while ((line = sr.ReadLine()) != null)
                {
                    string[] parts = line.Split(',');
                    if (parts.Length >= 4)
                    {
                        total++;
                        users.Add(parts[1]);
                        string ts = parts[2];
                        if (earliest == null || string.Compare(ts, earliest) < 0) earliest = ts;
                        if (latest == null || string.Compare(ts, latest) > 0) latest = ts;
                        if (parts[3] == "IN") inCount++;
                        else if (parts[3] == "OUT") outCount++;
                    }
                }
            }

            Console.WriteLine("  Total records : {0}  (IN: {1}, OUT: {2})", total, inCount, outCount);
            Console.WriteLine("  Unique users  : {0}", users.Count);
            Console.WriteLine("  Earliest log  : {0}", earliest ?? "N/A");
            Console.WriteLine("  Latest log    : {0}", latest ?? "N/A");
            Console.WriteLine("  Synced to ERPNext : {0}", stateService.Count);
            Console.WriteLine("  Pending sync      : {0}", total - stateService.Count);
        }
    }
}
