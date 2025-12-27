import asyncio
import os
import sqlite3
import threading
from functools import lru_cache
from typing import List, Optional


@lru_cache(maxsize=1)
def get_client() -> sqlite3.Connection:
    db_path = os.getenv("DB_PATH", "channelblam.db")
    if directory := os.path.dirname(db_path):
        os.makedirs(directory, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


_db_lock = threading.Lock()


async def ensure_schema(client: Optional[sqlite3.Connection] = None) -> None:
    db = client or get_client()

    def _create():
        with _db_lock:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS channel_blammed (
                    channel_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (channel_id, user_id)
                );
                """
            )
            db.execute(  # idv_required_level: 0, 1, 2 (none, all IDV, IDV <18)
                """
                CREATE TABLE IF NOT EXISTS channel_filters (
                    channel_id TEXT NOT NULL,
                    idv_required_level INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (channel_id)
                )

                """
            )
            # whitelist table
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS channel_whitelist (
                    channel_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    PRIMARY KEY (channel_id, user_id)
                );
                """
            )
            db.commit()

    await asyncio.to_thread(_create)


async def get_idv_required_level(
    channel_id: str, client: Optional[sqlite3.Connection] = None
) -> int:
    db = client or get_client()

    def _query():
        with _db_lock:
            cur = db.execute(
                "SELECT idv_required_level FROM channel_filters WHERE channel_id = ?;",
                (channel_id,),
            )
            row = cur.fetchone()
            if row:
                return row["idv_required_level"]
            return 0

    return await asyncio.to_thread(_query)


async def set_idv_required_level(
    channel_id: str,
    level: int,
    client: Optional[sqlite3.Connection] = None,
) -> None:
    db = client or get_client()

    def _exec():
        with _db_lock:
            db.execute(
                """
                INSERT INTO channel_filters (channel_id, idv_required_level)
                VALUES (?, ?)
                ON CONFLICT(channel_id) DO UPDATE SET idv_required_level=excluded.idv_required_level;
                """,
                (channel_id, level),
            )
            db.commit()

    await asyncio.to_thread(_exec)


async def add_blam(
    channel_id: str,
    user_id: str,
    client: Optional[sqlite3.Connection] = None,
) -> None:
    db = client or get_client()

    def _exec():
        with _db_lock:
            db.execute(
                "INSERT OR IGNORE INTO channel_blammed (channel_id, user_id) VALUES (?, ?);",
                (channel_id, user_id),
            )
            db.commit()

    await asyncio.to_thread(_exec)


async def list_blammed(
    channel_id: str, client: Optional[sqlite3.Connection] = None
) -> List[str]:
    db = client or get_client()

    def _query():
        with _db_lock:
            cur = db.execute(
                "SELECT user_id FROM channel_blammed WHERE channel_id = ? ORDER BY created_at DESC;",
                (channel_id,),
            )
            return [str(row[0]) for row in cur.fetchall()]

    return await asyncio.to_thread(_query)


async def remove_blam(
    channel_id: str, user_id: str, client: Optional[sqlite3.Connection] = None
) -> None:
    db = client or get_client()

    def _exec():
        with _db_lock:
            db.execute(
                "DELETE FROM channel_blammed WHERE channel_id = ? AND user_id = ?;",
                (channel_id, user_id),
            )
            db.commit()

    await asyncio.to_thread(_exec)


async def add_whitelist(
    channel_id: str,
    user_id: str,
    client: Optional[sqlite3.Connection] = None,
) -> None:
    db = client or get_client()

    def _exec():
        with _db_lock:
            db.execute(
                "INSERT OR IGNORE INTO channel_whitelist (channel_id, user_id) VALUES (?, ?);",
                (channel_id, user_id),
            )
            db.commit()

    await asyncio.to_thread(_exec)


async def remove_whitelist(
    channel_id: str, user_id: str, client: Optional[sqlite3.Connection] = None
) -> None:
    db = client or get_client()

    def _exec():
        with _db_lock:
            db.execute(
                "DELETE FROM channel_whitelist WHERE channel_id = ? AND user_id = ?;",
                (channel_id, user_id),
            )
            db.commit()

    await asyncio.to_thread(_exec)


async def list_whitelisted(
    channel_id: str, client: Optional[sqlite3.Connection] = None
) -> List[str]:
    db = client or get_client()

    def _query():
        with _db_lock:
            cur = db.execute(
                "SELECT user_id FROM channel_whitelist WHERE channel_id = ?;",
                (channel_id,),
            )
            return [str(row[0]) for row in cur.fetchall()]

    return await asyncio.to_thread(_query)
