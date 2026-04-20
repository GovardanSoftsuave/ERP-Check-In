from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    SmallInteger,
    String,
    Table,
    UniqueConstraint,
    create_engine,
    func,
    insert,
    select,
    text,
    update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from .models import AttendanceLog

log = logging.getLogger(__name__)

metadata = MetaData()

# attendance_logs.pushed_to_erp: 0=pending (send / retry), 1=ok or duplicate on ERP, 2=no_employee (do not resend)
ERP_PUSH_PENDING = 0
ERP_PUSH_DONE = 1
ERP_PUSH_NO_EMPLOYEE = 2

attendance_logs = Table(
    "attendance_logs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("source_ip", String(50), nullable=False),
    Column("user_id", String(50), nullable=False),
    Column("timestamp", DateTime, nullable=False),
    Column("log_type", String(10), nullable=False),
    Column("check_type", String(20), nullable=False),
    Column("verify_mode", String(50), nullable=False),
    Column("workcode", Integer, nullable=False, server_default="0"),
    Column("device_id", Integer, nullable=False),
    Column("pushed_to_erp", SmallInteger, nullable=False, server_default=text("0")),
    Column("created_at", DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    UniqueConstraint("source_ip", "user_id", "timestamp", name="unique_log"),
)


def create_mysql_engine(mysql_url: str) -> Engine:
    return create_engine(
        mysql_url,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        pool_recycle=1800,
        future=True,
    )


def init_db(engine: Engine) -> None:
    metadata.create_all(engine)


def get_last_timestamp(engine: Engine, source_ip: str) -> datetime | None:
    """Return MAX(timestamp) for a given device, or None if no rows exist."""
    stmt = select(func.max(attendance_logs.c.timestamp)).where(
        attendance_logs.c.source_ip == source_ip
    )
    with engine.connect() as conn:
        return conn.execute(stmt).scalar()


def insert_logs(engine: Engine, logs: list[AttendanceLog]) -> int:
    if not logs:
        return 0

    rows = [
        {
            "source_ip": x.source_ip,
            "user_id": x.user_id,
            "timestamp": x.timestamp,
            "log_type": x.log_type,
            "check_type": x.check_type,
            "verify_mode": x.verify_mode,
            "workcode": x.workcode,
            "device_id": x.device_id,
        }
        for x in logs
    ]

    stmt = insert(attendance_logs).prefix_with("IGNORE")

    with engine.begin() as conn:
        try:
            res = conn.execute(stmt, rows)
            return int(getattr(res, "rowcount", 0) or 0)
        except IntegrityError:
            inserted = 0
            for row in rows:
                try:
                    conn.execute(stmt, row)
                    inserted += 1
                except IntegrityError:
                    continue
            return inserted


def fetch_logs(
    engine: Engine,
    start_time: datetime,
    end_time: datetime,
    limit: int = 5000,
    pushed_to_erp: bool | None = None,
) -> list[dict]:
    cols = [
        attendance_logs.c.source_ip,
        attendance_logs.c.user_id,
        attendance_logs.c.timestamp,
        attendance_logs.c.log_type,
        attendance_logs.c.check_type,
        attendance_logs.c.verify_mode,
        attendance_logs.c.workcode,
        attendance_logs.c.device_id,
        attendance_logs.c.pushed_to_erp,
    ]
    stmt = (
        select(*cols)
        .where(attendance_logs.c.timestamp >= start_time)
        .where(attendance_logs.c.timestamp <= end_time)
    )
    if pushed_to_erp is not None:
        if pushed_to_erp:
            stmt = stmt.where(attendance_logs.c.pushed_to_erp == ERP_PUSH_DONE)
        else:
            stmt = stmt.where(attendance_logs.c.pushed_to_erp == ERP_PUSH_PENDING)
    stmt = stmt.order_by(attendance_logs.c.timestamp.asc()).limit(limit)

    with engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
        return [dict(r) for r in rows]


def mark_pushed_to_erp(engine: Engine, log_ids: list[int]) -> int:
    """Mark rows as successfully handled on ERP (ok or duplicate)."""
    if not log_ids:
        return 0
    stmt = (
        update(attendance_logs)
        .where(attendance_logs.c.id.in_(log_ids))
        .values(pushed_to_erp=ERP_PUSH_DONE)
    )
    with engine.begin() as conn:
        res = conn.execute(stmt)
        return int(getattr(res, "rowcount", 0) or 0)


def mark_erp_no_employee(engine: Engine, log_ids: list[int]) -> int:
    """Mark rows ERP could not map to an employee; excluded from future push batches."""
    if not log_ids:
        return 0
    stmt = (
        update(attendance_logs)
        .where(attendance_logs.c.id.in_(log_ids))
        .values(pushed_to_erp=ERP_PUSH_NO_EMPLOYEE)
    )
    with engine.begin() as conn:
        res = conn.execute(stmt)
        return int(getattr(res, "rowcount", 0) or 0)
