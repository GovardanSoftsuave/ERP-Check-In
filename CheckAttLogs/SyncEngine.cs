using System;
using System.Collections.Generic;
using System.Threading;
using CheckAttLogs.Config;
using CheckAttLogs.Interfaces;
using CheckAttLogs.Models;
using CheckAttLogs.Services;

namespace CheckAttLogs
{
    public class SyncEngine
    {
        private readonly AppSettings _settings;
        private readonly IDeviceService _deviceService;
        private readonly IErpNextService _erpService;
        private readonly ISyncStateService _stateService;
        private readonly ICsvService _csvService;

        private readonly HashSet<string> _seenRecords = new HashSet<string>();
        private readonly object _lock = new object();  // protects shared state
        private volatile bool _running = true;

        private static readonly string SEP  = new string('-', 110);
        private static readonly string SEP2 = new string('=', 110);

        public SyncEngine(
            AppSettings settings,
            IDeviceService deviceService,
            IErpNextService erpService,
            ISyncStateService stateService,
            ICsvService csvService)
        {
            _settings = settings;
            _deviceService = deviceService;
            _erpService = erpService;
            _stateService = stateService;
            _csvService = csvService;
        }

        // ─── SYNC MODE (multi-threaded) ──────────────────────────────

        public void RunSync(int pollSeconds)
        {
            PrintHeader(string.Format("ZK -> ERPNEXT SYNC SERVICE  (every {0}s)  [{1} device(s), threaded]",
                pollSeconds, _settings.Devices.Length));

            foreach (var d in _settings.Devices)
                Console.WriteLine("  {0}  {1}:{2}  LogType={3}  DeviceId={4}",
                    d.Label, d.IP, d.Port, d.LogType, d.DeviceId);

            Console.WriteLine("  ERPNext   : {0}", _settings.ErpNext.Url);
            Console.WriteLine("  State     : {0}", _settings.Sync.StateFile);
            Console.WriteLine("  StartDate : {0}", _settings.Sync.StartDate ?? "(all)");
            Console.WriteLine("  ID Map    : {0} entries", _settings.EmployeeIdMap.Count);
            Console.WriteLine();

            // Load persisted state
            _stateService.Load();
            Log("Loaded {0} previously synced records from state file.", _stateService.Count);

            lock (_seenRecords)
            {
                foreach (var key in _csvService.LoadSeenKeys())
                    _seenRecords.Add(key);
            }
            Log("Loaded {0} existing records from CSV.", _seenRecords.Count);

            // Connect to all devices
            int connectedCount = 0;
            foreach (var d in _settings.Devices)
            {
                if (_deviceService.Connect(d))
                    connectedCount++;
            }

            if (connectedCount == 0)
            {
                Log("ERROR: No devices connected. Exiting.");
                return;
            }

            Log("{0}/{1} devices connected.", connectedCount, _settings.Devices.Length);
            Console.WriteLine("  Each device runs on its own thread. Press Ctrl+C to stop.\n");

            // Graceful shutdown
            Console.CancelKeyPress += (sender, e) =>
            {
                e.Cancel = true;
                _running = false;
                Console.WriteLine("\n  Stopping all threads...");
            };

            // Start one thread per device
            var threads = new Thread[_settings.Devices.Length];
            for (int i = 0; i < _settings.Devices.Length; i++)
            {
                DeviceConfig device = _settings.Devices[i];
                int pollSec = pollSeconds;
                threads[i] = new Thread(() => DeviceSyncLoop(device, pollSec));
                threads[i].Name = device.Label;
                threads[i].IsBackground = true;
                threads[i].Start();
                Log("  Thread started: {0}", device.Label);
            }

            // Wait for all threads to finish
            foreach (var t in threads)
                t.Join();

            // Clean shutdown
            Log("Disconnecting...");
            _deviceService.DisconnectAll();
            lock (_lock) { _stateService.Save(); }
            Log("Done. Total synced records: {0}", _stateService.Count);
        }

        // ─── Per-device sync loop (runs on its own thread) ──────────

        private void DeviceSyncLoop(DeviceConfig device, int pollSeconds)
        {
            int cycle = 0;
            int totalPushed = 0;
            int totalErrors = 0;
            string tag = "[" + device.Label + "]";

            while (_running)
            {
                cycle++;
                Log("{0} Cycle #{1}", tag, cycle);

                // Reconnect if needed
                if (!_deviceService.IsConnected(device))
                {
                    if (!_deviceService.TryReconnect(device))
                    {
                        Log("{0} Not connected, retrying in {1}s...", tag, pollSeconds);
                        WaitSeconds(pollSeconds);
                        continue;
                    }
                }

                // 1. Read logs from this device
                var allRecords = _deviceService.ReadLogs(device, _settings.Sync.StartDate);
                Log("{0} {1} records read from device.", tag, allRecords.Count);

                // 2. Filter new records (thread-safe)
                var newRecords = new List<AttRecord>();
                lock (_seenRecords)
                {
                    foreach (var r in allRecords)
                    {
                        string key = r.SourceIP + "|" + r.UserID + "|" + r.Timestamp + "|" + r.CheckType;
                        if (_seenRecords.Add(key))
                            newRecords.Add(r);
                    }
                }

                if (newRecords.Count > 0)
                {
                    Log("{0} {1} new record(s).", tag, newRecords.Count);
                    lock (_lock) { _csvService.Save(newRecords); }
                }

                // 3. Find unpushed records
                var toPush = new List<AttRecord>();
                lock (_lock)
                {
                    foreach (var r in allRecords)
                    {
                        string pushKey = r.SourceIP + "|" + r.UserID + "|" + r.Timestamp;
                        if (!_stateService.IsPushed(pushKey))
                            toPush.Add(r);
                    }
                }

                if (toPush.Count == 0)
                {
                    Log("{0} All records synced.", tag);
                    WaitSeconds(pollSeconds);
                    continue;
                }

                Log("{0} Pushing {1} record(s) to ERPNext...", tag, toPush.Count);
                int pushed = 0, errors = 0, skipped = 0;

                foreach (var r in toPush)
                {
                    if (!_running) break;

                    string pushKey = r.SourceIP + "|" + r.UserID + "|" + r.Timestamp;

                    if (_erpService.IsUnknownUser(r.UserID))
                    {
                        skipped++;
                        continue;
                    }

                    string mappedId = r.UserID;
                    if (_settings.EmployeeIdMap.ContainsKey(r.UserID))
                        mappedId = _settings.EmployeeIdMap[r.UserID];

                    Console.Write("{0} [{1}/{2}] {3} User={4}->{5}  Time={6}  DevId={7} ... ",
                        tag, pushed + errors + 1, toPush.Count - skipped,
                        r.FixedLogType, r.UserID, mappedId, r.Timestamp, r.FixedDeviceId);

                    PushResult result = _erpService.Push(r);

                    switch (result)
                    {
                        case PushResult.Success:
                        case PushResult.Duplicate:
                            lock (_lock) { _stateService.MarkPushed(pushKey); }
                            pushed++;
                            Console.WriteLine("OK");
                            break;

                        case PushResult.NoEmployee:
                            errors++;
                            break;

                        default:
                            errors++;
                            Console.WriteLine("FAILED");
                            break;
                    }

                    Thread.Sleep(1000);
                }

                // Save state after batch
                lock (_lock) { _stateService.Save(); }

                totalPushed += pushed;
                totalErrors += errors;
                Log("{0} Pushed: {1}  Errors: {2}  Skipped: {3}  (Total synced: {4})",
                    tag, pushed, errors, skipped, _stateService.Count);

                var unknowns = _erpService.GetUnknownUsers();
                if (unknowns.Count > 0)
                    Log("{0} Unknown user IDs: {1}", tag, string.Join(", ", unknowns));

                // Wait for next cycle
                WaitSeconds(pollSeconds);
            }

            Log("{0} Thread stopped. Session pushed: {1}, errors: {2}", device.Label, totalPushed, totalErrors);
        }

        private void WaitSeconds(int seconds)
        {
            for (int s = 0; s < seconds && _running; s++)
                Thread.Sleep(1000);
        }

        // ─── FETCH-ONLY MODE ──────────────────────────────────────────

        public void FetchOnly()
        {
            PrintHeader(string.Format("FETCH ONLY - ALL RECORDS  [{0} device(s)]", _settings.Devices.Length));

            var allRecords = new List<AttRecord>();

            foreach (var d in _settings.Devices)
            {
                if (!_deviceService.Connect(d))
                    continue;

                var devRecords = _deviceService.ReadLogs(d, _settings.Sync.StartDate);
                Console.WriteLine("  {0}: {1} records.", d.Label, devRecords.Count);
                allRecords.AddRange(devRecords);
                _deviceService.Disconnect(d);
            }

            if (allRecords.Count == 0)
            {
                Console.WriteLine("\n  No records found.");
                return;
            }

            Console.WriteLine(SEP);
            Console.WriteLine("  {0,-16} {1,-10} {2,-22} {3,-10} {4,-12} {5}",
                "SOURCE", "USER ID", "TIMESTAMP", "LOG TYPE", "VERIFY", "WORKCODE");
            Console.WriteLine(SEP);
            foreach (var r in allRecords)
                Console.WriteLine("  {0,-16} {1,-10} {2,-22} {3,-10} {4,-12} {5}",
                    r.SourceIP, r.UserID, r.Timestamp, r.FixedLogType, r.VerifyMode, r.Workcode);
            Console.WriteLine(SEP);

            var users = new HashSet<string>();
            foreach (var r in allRecords) users.Add(r.UserID);
            Console.WriteLine("\n  Total records : {0}", allRecords.Count);
            Console.WriteLine("  Unique users  : {0}", users.Count);

            _csvService.Save(allRecords);
        }

        // ─── CSV SUMMARY ──────────────────────────────────────────────

        public void ShowCsvSummary()
        {
            PrintHeader("SAVED CSV SUMMARY - " + _settings.Sync.OutputCsv);
            _stateService.Load();
            ((CsvService)_csvService).PrintSummary(_stateService);
        }

        // ─── Helpers ──────────────────────────────────────────────────

        private void PrintHeader(string title)
        {
            Console.WriteLine("\n" + SEP2);
            Console.WriteLine("  " + title);
            Console.WriteLine(SEP2);
        }

        private static void Log(string format, params object[] args)
        {
            Console.WriteLine("  " + string.Format(format, args));
        }
    }
}
