"""
zk_sync — ZK Device -> ERPNext Attendance Sync Package

Structure mirrors the C# CheckAttLogs project:
    config.py           -> Config/AppSettings.cs
    models.py           -> Models/AttRecord.cs
    services/device.py  -> Services/ZkDeviceService.cs
    services/erpnext.py -> Services/ErpNextService.cs
    services/state.py   -> Services/SyncStateService.cs
    services/csv_svc.py -> Services/CsvService.cs
    engine.py           -> SyncEngine.cs
    main.py             -> Program.cs
"""
