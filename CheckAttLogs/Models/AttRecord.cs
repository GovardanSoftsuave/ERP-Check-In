namespace CheckAttLogs.Models
{
    public class AttRecord
    {
        public string SourceIP { get; set; }
        public string FixedLogType { get; set; }
        public string FixedDeviceId { get; set; }
        public string UserID { get; set; }
        public string Timestamp { get; set; }
        public string CheckType { get; set; }
        public string VerifyMode { get; set; }
        public string Workcode { get; set; }
    }

    public enum PushResult
    {
        Success,
        Duplicate,
        NoEmployee,
        Error
    }
}
