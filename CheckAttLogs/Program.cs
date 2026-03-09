using System;
using CheckAttLogs.Config;
using CheckAttLogs.Services;

namespace CheckAttLogs
{
    class Program
    {
        [STAThread]
        static void Main(string[] args)
        {
            try
            {
                // Load configuration from appsettings.json
                var settings = ConfigLoader.Load();

                // Parse CLI overrides
                int pollSeconds = settings.Sync.PollIntervalSeconds;
                bool csvOnly = false;
                bool fetchOnly = false;

                for (int i = 0; i < args.Length; i++)
                {
                    if (args[i] == "--csv")
                        csvOnly = true;
                    else if (args[i] == "--fetch-only")
                        fetchOnly = true;
                    else if (args[i] == "--poll" && i + 1 < args.Length)
                    {
                        int.TryParse(args[i + 1], out pollSeconds);
                        if (pollSeconds < 5) pollSeconds = 5;
                        i++;
                    }
                    else if (args[i] == "--url" && i + 1 < args.Length)
                    { settings.ErpNext.Url = args[i + 1]; i++; }
                    else if (args[i] == "--key" && i + 1 < args.Length)
                    { settings.ErpNext.ApiKey = args[i + 1]; i++; }
                    else if (args[i] == "--secret" && i + 1 < args.Length)
                    { settings.ErpNext.ApiSecret = args[i + 1]; i++; }
                }

                // Compose services (manual DI)
                var deviceService = new ZkDeviceService();
                var erpService    = new ErpNextService(settings.ErpNext, settings.EmployeeIdMap);
                var stateService  = new SyncStateService(settings.Sync.StateFile);
                var csvService    = new CsvService(settings.Sync.OutputCsv);

                var engine = new SyncEngine(settings, deviceService, erpService, stateService, csvService);

                // Run requested mode
                if (csvOnly)
                    engine.ShowCsvSummary();
                else if (fetchOnly)
                    engine.FetchOnly();
                else
                    engine.RunSync(pollSeconds);
            }
            catch (Exception ex)
            {
                Console.WriteLine("FATAL: {0}", ex.Message);
                Console.WriteLine(ex.StackTrace);
            }
        }
    }
}
