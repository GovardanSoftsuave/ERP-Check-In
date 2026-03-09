using System.Collections.Generic;
using CheckAttLogs.Models;

namespace CheckAttLogs.Interfaces
{
    public interface ICsvService
    {
        void Save(List<AttRecord> records);
        HashSet<string> LoadSeenKeys();
    }
}
