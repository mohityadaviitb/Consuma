"""
SQLite persistence layer for request tracking.
Uses aiosqlite for async database operations.
"""
import aiosqlite
import json
from datetime import datetime, timedelta
from typing import Optional, List
from contextlib import asynccontextmanager

from app.models import (
    RequestRecord, RequestMode, RequestStatus, WorkResult
)
from app.config import settings


DATABASE_PATH = "requests.db"


async def init_database():
    """Initialize database schema."""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS requests (
                id TEXT PRIMARY KEY,
                mode TEXT NOT NULL,
                status TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                callback_url TEXT,
                callback_attempts INTEGER DEFAULT 0,
                callback_last_error TEXT,
                result_json TEXT
            )
        """)
        
        # Index for efficient queries
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_requests_mode 
            ON requests(mode)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_requests_created_at 
            ON requests(created_at)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_requests_status 
            ON requests(status)
        """)
        
        await db.commit()


async def save_request(record: RequestRecord) -> None:
    """Save or update a request record."""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        result_json = record.result.model_dump_json() if record.result else None
        
        await db.execute("""
            INSERT OR REPLACE INTO requests 
            (id, mode, status, payload_hash, created_at, completed_at, 
             callback_url, callback_attempts, callback_last_error, result_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            record.id,
            record.mode.value,
            record.status.value,
            record.payload_hash,
            record.created_at.isoformat(),
            record.completed_at.isoformat() if record.completed_at else None,
            record.callback_url,
            record.callback_attempts,
            record.callback_last_error,
            result_json
        ))
        await db.commit()


async def get_request(request_id: str) -> Optional[RequestRecord]:
    """Get a single request by ID."""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM requests WHERE id = ?", (request_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return _row_to_record(row)
    return None


async def list_requests(
    mode: Optional[RequestMode] = None,
    limit: int = 100,
    offset: int = 0
) -> tuple[int, List[RequestRecord]]:
    """List requests with optional mode filter."""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        
        # Build query
        where_clause = ""
        params: list = []
        if mode:
            where_clause = "WHERE mode = ?"
            params.append(mode.value)
        
        # Get total count
        async with db.execute(
            f"SELECT COUNT(*) as count FROM requests {where_clause}",
            params
        ) as cursor:
            row = await cursor.fetchone()
            total = row["count"]
        
        # Get paginated results
        query = f"""
            SELECT * FROM requests {where_clause}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])
        
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            records = [_row_to_record(row) for row in rows]
        
        return total, records


async def cleanup_old_requests() -> int:
    """Delete requests older than retention period. Returns count deleted."""
    cutoff = datetime.utcnow() - timedelta(hours=settings.request_retention_hours)
    
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM requests WHERE created_at < ?",
            (cutoff.isoformat(),)
        )
        deleted = cursor.rowcount
        await db.commit()
        return deleted


async def check_database_health() -> bool:
    """Quick database health check."""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as db:
            async with db.execute("SELECT 1") as cursor:
                await cursor.fetchone()
        return True
    except Exception:
        return False


def _row_to_record(row) -> RequestRecord:
    """Convert database row to RequestRecord."""
    result = None
    if row["result_json"]:
        result = WorkResult.model_validate_json(row["result_json"])
    
    return RequestRecord(
        id=row["id"],
        mode=RequestMode(row["mode"]),
        status=RequestStatus(row["status"]),
        payload_hash=row["payload_hash"],
        created_at=datetime.fromisoformat(row["created_at"]),
        completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
        callback_url=row["callback_url"],
        callback_attempts=row["callback_attempts"],
        callback_last_error=row["callback_last_error"],
        result=result
    )
