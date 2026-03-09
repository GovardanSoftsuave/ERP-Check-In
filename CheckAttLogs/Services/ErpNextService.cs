using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Text;
using CheckAttLogs.Config;
using CheckAttLogs.Interfaces;
using CheckAttLogs.Models;

namespace CheckAttLogs.Services
{
    public class ErpNextService : IErpNextService
    {
        private readonly ErpNextConfig _config;
        private readonly Dictionary<string, string> _employeeIdMap;
        private readonly HashSet<string> _unknownUsers = new HashSet<string>();

        public ErpNextService(ErpNextConfig config, Dictionary<string, string> employeeIdMap)
        {
            _config = config;
            _employeeIdMap = employeeIdMap ?? new Dictionary<string, string>();
        }

        public bool IsUnknownUser(string userId)
        {
            return _unknownUsers.Contains(userId);
        }

        public HashSet<string> GetUnknownUsers()
        {
            return _unknownUsers;
        }

        public PushResult Push(AttRecord record)
        {
            if (_unknownUsers.Contains(record.UserID))
                return PushResult.NoEmployee;

            try
            {
                // Map device UserID to ERPNext employee_field_value
                string employeeFieldValue = record.UserID;
                if (_employeeIdMap.ContainsKey(record.UserID))
                    employeeFieldValue = _employeeIdMap[record.UserID];

                string json = string.Format(
                    "{{\"employee_field_value\":\"{0}\",\"timestamp\":\"{1}\",\"device_id\":\"{2}\",\"log_type\":\"{3}\"}}",
                    EscapeJson(employeeFieldValue),
                    EscapeJson(record.Timestamp),
                    EscapeJson(record.FixedDeviceId),
                    record.FixedLogType);

                string url = _config.Url.TrimEnd('/') +
                    "/api/method/hrms.hr.doctype.employee_checkin.employee_checkin.add_log_based_on_employee_field";

                // Log request
                Console.WriteLine();
                Console.WriteLine("      POST {0}", url);
                Console.WriteLine("      Payload: {0}", json);

                var req = (HttpWebRequest)WebRequest.Create(url);
                req.Method = "POST";
                req.ContentType = "application/json";
                req.Accept = "application/json";
                req.Headers["Authorization"] = "token " + _config.ApiKey + ":" + _config.ApiSecret;
                req.Timeout = 30000;

                byte[] body = Encoding.UTF8.GetBytes(json);
                req.ContentLength = body.Length;
                using (var s = req.GetRequestStream())
                    s.Write(body, 0, body.Length);

                using (var resp = (HttpWebResponse)req.GetResponse())
                {
                    string respBody = "";
                    using (var sr = new StreamReader(resp.GetResponseStream()))
                        respBody = sr.ReadToEnd();

                    Console.WriteLine("      Response: {0} {1}", (int)resp.StatusCode, resp.StatusCode);
                    if (respBody.Length > 0)
                        Console.WriteLine("      Body: {0}", Truncate(respBody, 300));

                    if (resp.StatusCode == HttpStatusCode.OK || (int)resp.StatusCode == 201)
                        return PushResult.Success;

                    return PushResult.Error;
                }
            }
            catch (WebException wex)
            {
                return HandleWebException(wex, record);
            }
            catch (Exception ex)
            {
                Console.WriteLine("      -> ERR: {0}", ex.Message);
                return PushResult.Error;
            }
        }

        private PushResult HandleWebException(WebException wex, AttRecord record)
        {
            if (wex.Response == null)
            {
                Console.WriteLine("      -> NET_ERR: {0}", wex.Message);
                return PushResult.Error;
            }

            var errResp = (HttpWebResponse)wex.Response;
            string errBody;
            using (var sr = new StreamReader(errResp.GetResponseStream()))
                errBody = sr.ReadToEnd();

            Console.WriteLine();
            Console.WriteLine("      Response: {0}", (int)errResp.StatusCode);
            Console.WriteLine("      Body: {0}", Truncate(errBody, 300));

            // Duplicate check-in
            if ((int)errResp.StatusCode == 409
                || errBody.Contains("DuplicateEntryError")
                || errBody.Contains("already has a log with the same timestamp"))
            {
                Console.Write("      -> DUPLICATE (marking as synced) ... ");
                return PushResult.Duplicate;
            }

            // Employee not found
            if (errBody.Contains("No Employee found"))
            {
                _unknownUsers.Add(record.UserID);
                Console.WriteLine("      -> SKIP (no employee for ID={0})", record.UserID);
                return PushResult.NoEmployee;
            }

            Console.WriteLine("      -> ERR({0}): {1}", (int)errResp.StatusCode, Truncate(errBody, 200));
            return PushResult.Error;
        }

        private static string EscapeJson(string s)
        {
            return s.Replace("\\", "\\\\").Replace("\"", "\\\"");
        }

        private static string Truncate(string s, int maxLen)
        {
            return s.Length > maxLen ? s.Substring(0, maxLen) + "..." : s;
        }
    }
}
