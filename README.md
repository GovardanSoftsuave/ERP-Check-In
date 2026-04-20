# ERP Attendance Check-in System

Real-time attendance ingestion from ZKTeco biometric devices to ERPNext.

## Architecture

```
ZK Biometric Devices (IN/OUT)
        │
        ▼
┌─────────────────────────────────┐
│  DeviceWorker threads (1 per    │   Polls each device every 60s
│  device via COM SDK)            │   via zkemkeeper.ZKEM
└───────────┬─────────────────────┘
            │
            ▼
┌─────────────────────────────────┐
│  Redis Queue                    │   Buffered log entries
│  (global:logs_queue)            │
└───────────┬─────────────────────┘
            │
            ▼
┌─────────────────────────────────┐
│  AggregatorWorker               │   Drains Redis → MySQL
│  (dedup + batch insert)         │   (INSERT IGNORE on unique key)
└───────────┬─────────────────────┘
            │
            ▼
┌─────────────────────────────────┐
│  MySQL (attendance_logs table)  │   Source of truth
└───────────┬─────────────────────┘
            │
            ▼
┌─────────────────────────────────┐
│  erp_push.py                    │   Pushes pending records
│  (standalone script)            │   to ERPNext bulk API
└─────────────────────────────────┘
```

## Workers (Threads)

| Worker | Count | Role |
|--------|-------|------|
| **DeviceWorker** | 1 per device (currently 4) | Connects to ZK device, reads attendance logs, pushes to Redis |
| **AggregatorWorker** | 1 (shared) | Drains Redis queue, deduplicates, inserts into MySQL |
| **FailedRetryWorker** | 1 (shared) | Retries failed DB inserts from a separate Redis queue |

Adding more devices automatically adds more DeviceWorker threads. The Aggregator and FailedRetry workers stay at 1 each.

## Current Devices

| Device | IP | Type | Device ID |
|--------|-----|------|-----------|
| OUT-Device(61) | 192.168.12.61 | OUT | 9 |
| IN-Device(62) | 192.168.12.62 | IN | 8 |
| OUT-Device(63) | 192.168.12.63 | OUT | 11 |
| IN-Device(64) | 192.168.12.64 | IN | 10 |

## Prerequisites

- **Windows** (required for ZKTeco COM SDK `zkemkeeper.ZKEM`)
- **Python 3.10+**
- **Docker** (for Redis and MySQL)
- ZKTeco biometric devices on the network

## Setup

### 1. Start Redis and MySQL

```powershell
docker-compose up -d
```

This starts:
- Redis on port `6379`
- MySQL on port `3306` (root/root, database: `attendance_prod`, timezone: IST)

### 2. Create virtual environment and install dependencies

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure devices

Edit `attendance_ingest/config.py`:

- **`DEVICES`** — Add/remove ZK device entries (ip, port, machine_id, password, label, log_type, device_id)
- **`TESTING_MODE`** — Set `True` for mock devices (no hardware needed), `False` for production
- **`MYSQL_URL`** — MySQL connection string
- **`REDIS_URL`** — Redis connection string
- **`ERPNEXT_URL`** / **`ERPNEXT_API_KEY`** / **`ERPNEXT_API_SECRET`** — ERPNext API credentials
- **`EMPLOYEE_ID_MAP`** — Maps numeric device enroll IDs to ERPNext employee IDs (e.g. `"21" → "SS0021"`)

### 4. Run the main service (device polling + API)

```powershell
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

On startup it will:
- Connect to all configured devices
- Start polling every 60 seconds
- Backfill from `START_DATE` if no prior data exists for a device
- Expose the FastAPI endpoints on port 8000

### 5. Run the ERP push service (separate terminal)

```powershell
python erp_push.py
```

This runs independently — fetches pending records from MySQL and pushes them to ERPNext in batches.

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | System health — Redis ping, device count, mode |
| `/devices` | GET | List all devices with their last polled timestamp |
| `/logs` | GET | Query attendance logs with filters |

### Query logs

```
GET /logs?start_time=2026-04-20T00:00:00&end_time=2026-04-20T23:59:59
GET /logs?start_time=2026-04-20T00:00:00&end_time=2026-04-20T23:59:59&pushed_to_erp=false
GET /logs?start_time=2026-04-20T00:00:00&end_time=2026-04-20T23:59:59&limit=100
```

## Logs

All logs are written to the `logs/` directory:

| File | Contents |
|------|----------|
| `attendance.log` | Main system log (connections, polling, errors) |
| `device_{id}_{type}.log` | Per-device log (e.g. `device_9_OUT.log`) |
| `erp_push.log` | ERP push results (OK/SKIP/FAIL per record) |

## Adding a New Device

1. Edit `attendance_ingest/config.py` and add an entry to the `DEVICES` list:

```python
{
    "ip": "192.168.12.65",
    "port": 4370,
    "machine_id": 1,
    "password": "000000",
    "label": "IN-Device(65)",
    "log_type": "IN",       # "IN" or "OUT"
    "device_id": 12,        # unique integer
},
```

2. Restart the main service. The new device will backfill automatically.

Existing devices are unaffected — they resume from where they left off.

## Key Configuration (config.py)

| Setting | Default | Description |
|---------|---------|-------------|
| `TESTING_MODE` | `False` | `True` = mock devices, `False` = real hardware |
| `POLL_SECONDS` | `60` | How often each device is polled (seconds) |
| `ERP_PUSH_INTERVAL` | `30` | Seconds between ERP push cycles |
| `ERP_PUSH_BATCH_SIZE` | `200` | Records per ERP push cycle |
| `ERP_PUSH_MODE` | `"bulk"` | `"bulk"` (one API call) or `"single"` (one call per record) |
| `START_DATE` | `"2026-04-09 00:00:00"` | Backfill start date for new devices |

## Redis Keys

All keys are prefixed with `REDIS_KEY_PREFIX` (default `test:`):

| Key | Purpose |
|-----|---------|
| `{prefix}device:{ip}:logs_queue` | Per-device log buffer |
| `{prefix}global:logs_queue` | Main queue consumed by AggregatorWorker |
| `{prefix}global:failed_logs_queue` | Failed inserts waiting for retry |
| `{prefix}global:failed_logs_processing_queue` | Items currently being retried |

## MySQL Schema

Table: `attendance_logs`

| Column | Type | Description |
|--------|------|-------------|
| `id` | INT (PK) | Auto-increment |
| `source_ip` | VARCHAR(50) | Device IP |
| `user_id` | VARCHAR(50) | Employee enroll number |
| `timestamp` | DATETIME | Check-in/out time |
| `log_type` | VARCHAR(10) | "IN" or "OUT" |
| `check_type` | VARCHAR(20) | CHECK-IN, CHECK-OUT, BREAK-OUT, etc. |
| `verify_mode` | VARCHAR(50) | Fingerprint, Face, RFID Card, etc. |
| `workcode` | INT | Device workcode |
| `device_id` | INT | Device identifier |
| `pushed_to_erp` | SMALLINT | 0=pending, 1=ok/duplicate, 2=no_employee |
| `created_at` | DATETIME | Row creation time |

Unique constraint: `(source_ip, user_id, timestamp)`

## Reliability Features

- **Auto-reconnect**: Devices that go stale are proactively reconnected every 30 minutes
- **Backoff retry**: Failed device connections retry with exponential backoff (5s → 10s → 20s → 40s → 60s max)
- **Duplicate protection**: `INSERT IGNORE` on unique constraint prevents duplicate records
- **Failed insert retry**: If MySQL insert fails, records go to a retry queue and are re-attempted
- **ERP push tracking**: Each record tracks its push status (pending/done/no_employee) — errors stay pending and are retried in the next cycle
- **Graceful empty devices**: New devices with no check-in data poll silently without errors
