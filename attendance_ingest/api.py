from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from .db import get_last_timestamp
from .runtime import runtime_singleton

router = APIRouter()


@router.get("/health")
def health() -> dict:
    rt = runtime_singleton()
    return {
        "ok": True,
        "testing_mode": rt.settings.testing_mode if rt.settings else None,
        "redis_key_prefix": rt.settings.redis_key_prefix if rt.settings else None,
        "redis": rt.redis.ping() if rt.redis else False,
        "devices": len(rt.settings.devices) if rt.settings else 0,
    }


@router.get("/devices")
def devices() -> list[dict]:
    rt = runtime_singleton()
    out: list[dict] = []
    for d in rt.settings.devices:
        last_ts = get_last_timestamp(rt.engine, d.ip) if rt.engine else None
        out.append(
            {
                "ip": d.ip,
                "port": d.port,
                "machine_id": d.machine_id,
                "label": d.label,
                "log_type": d.log_type,
                "device_id": d.device_id,
                "last_timestamp": last_ts.isoformat(sep=" ", timespec="seconds") if last_ts else None,
            }
        )
    return out


@router.get("/logs")
def logs(
    start_time: datetime = Query(...),
    end_time: datetime = Query(...),
    pushed_to_erp: Optional[bool] = Query(
        None,
        description="Filter: true=ERP ok/dup (1), false=pending retry (0 only), omit=all",
    ),
    limit: int = Query(5000, ge=1, le=20000),
) -> dict:
    rt = runtime_singleton()
    if end_time < start_time:
        raise HTTPException(status_code=400, detail="end_time must be >= start_time")
    rows = rt.fetch_logs(start_time, end_time, limit=limit, pushed_to_erp=pushed_to_erp)
    return {"count": len(rows), "items": rows}
