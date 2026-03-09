using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using CheckAttLogs.Interfaces;

namespace CheckAttLogs.Services
{
    public class SyncStateService : ISyncStateService
    {
        private readonly string _filePath;
        private readonly HashSet<string> _pushedRecords = new HashSet<string>();

        public SyncStateService(string filePath)
        {
            _filePath = filePath;
        }

        public int Count
        {
            get { return _pushedRecords.Count; }
        }

        public void Load()
        {
            if (!File.Exists(_filePath))
                return;

            try
            {
                using (var sr = new StreamReader(_filePath, Encoding.UTF8))
                {
                    string line;
                    while ((line = sr.ReadLine()) != null)
                    {
                        line = line.Trim();
                        if (!string.IsNullOrEmpty(line))
                            _pushedRecords.Add(line);
                    }
                }
            }
            catch (Exception ex)
            {
                Console.WriteLine("  WARN: Could not load state file: {0}", ex.Message);
            }
        }

        public void Save()
        {
            try
            {
                using (var sw = new StreamWriter(_filePath, false, Encoding.UTF8))
                {
                    foreach (string key in _pushedRecords)
                        sw.WriteLine(key);
                }
            }
            catch (Exception ex)
            {
                Console.WriteLine("  WARN: Could not save state file: {0}", ex.Message);
            }
        }

        public bool IsPushed(string key)
        {
            return _pushedRecords.Contains(key);
        }

        public void MarkPushed(string key)
        {
            _pushedRecords.Add(key);
        }
    }
}
