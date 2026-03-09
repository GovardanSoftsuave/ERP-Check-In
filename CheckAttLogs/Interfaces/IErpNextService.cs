using System.Collections.Generic;
using CheckAttLogs.Models;

namespace CheckAttLogs.Interfaces
{
    public interface IErpNextService
    {
        PushResult Push(AttRecord record);
        bool IsUnknownUser(string userId);
        HashSet<string> GetUnknownUsers();
    }
}
