namespace CheckAttLogs.Interfaces
{
    public interface ISyncStateService
    {
        void Load();
        void Save();
        bool IsPushed(string key);
        void MarkPushed(string key);
        int Count { get; }
    }
}
