import asyncio
import sqlite3
import threading
from functools import lru_cache
from typing import List, Optional


@lru_cache(maxsize=1)
def get_client() -> sqlite3.Connection:
    conn = sqlite3.connect("channelblam.db", check_same_thread=False)
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
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS channel_managers (
                    channel_id TEXT PRIMARY KEY,
                    manager_user_id TEXT NOT NULL,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS oauth_tokens (
                    team_id TEXT PRIMARY KEY,
                    user_token TEXT NOT NULL,
                    installer_user_id TEXT,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            db.commit()

    await asyncio.to_thread(_create)


async def add_blam(
    channel_id: str,
    user_id: str,
    blammed_by: str | None = None,
    client: Optional[sqlite3.Connection] = None,
) -> None:
    db = client or get_client()

    def _exec():
        with _db_lock:
            db.execute(
                "INSERT OR IGNORE INTO channel_blammed (channel_id, user_id) VALUES (?, ?);",
                (channel_id, user_id),
            )
            if blammed_by:
                db.execute(
                    """
                    INSERT INTO channel_managers (channel_id, manager_user_id)
                    VALUES (?, ?)
                    ON CONFLICT(channel_id) DO UPDATE SET
                        manager_user_id=excluded.manager_user_id,
                        updated_at=CURRENT_TIMESTAMP;
                    """,
                    (channel_id, blammed_by),
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


async def get_managing_user(
    channel_id: str, client: Optional[sqlite3.Connection] = None
) -> str | None:
    db = client or get_client()

    def _query():
        with _db_lock:
            cur = db.execute(
                "SELECT manager_user_id FROM channel_managers WHERE channel_id = ?;",
                (channel_id,),
            )
            row = cur.fetchone()
            return str(row[0]) if row else None

    return await asyncio.to_thread(_query)


# async def set_user_token(
#     team_id: str,
#     user_token: str,
#     installer_user_id: Optional[str] = None,
#     client: Optional[sqlite3.Connection] = None,
# ) -> None:
#     db = client or get_client()

#     def _exec():
#         with _db_lock:
#             db.execute(
#                 """
#                 INSERT INTO oauth_tokens (team_id, user_token, installer_user_id)
#                 VALUES (?, ?, ?)
#                 ON CONFLICT(team_id) DO UPDATE SET
#                     user_token=excluded.user_token,
#                     installer_user_id=excluded.installer_user_id,
#                     updated_at=CURRENT_TIMESTAMP;
#                 """,
#                 (team_id, user_token, installer_user_id),
#             )
#             db.commit()

#     await asyncio.to_thread(_exec)


# async def get_user_token(
#     team_id: str, client: Optional[sqlite3.Connection] = None
# ) -> Optional[str]:
#     db = client or get_client()

#     def _query():
#         with _db_lock:
#             cur = db.execute(
#                 "SELECT user_token FROM oauth_tokens WHERE team_id = ?;",
#                 (team_id,),
#             )
#             row = cur.fetchone()
#             return str(row[0]) if row else None

#     return await asyncio.to_thread(_query)


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
