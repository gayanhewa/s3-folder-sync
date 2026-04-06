"""SQLite-based local state tracking."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass
class FileState:
    relative_path: str
    content_hash: str
    local_mtime: float
    last_synced_etag: str
    last_synced_at: str
    is_deleted: bool = False


class StateDB:
    """Track local file state in SQLite."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS file_state (
                relative_path TEXT PRIMARY KEY,
                content_hash TEXT NOT NULL,
                local_mtime REAL NOT NULL,
                last_synced_etag TEXT NOT NULL DEFAULT '',
                last_synced_at TEXT NOT NULL DEFAULT '',
                is_deleted INTEGER NOT NULL DEFAULT 0
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_deletes (
                relative_path TEXT PRIMARY KEY,
                deleted_at TEXT NOT NULL,
                propagate_after TEXT NOT NULL
            )
        """)
        self._conn.commit()

    def get(self, relative_path: str) -> FileState | None:
        row = self._conn.execute(
            "SELECT * FROM file_state WHERE relative_path = ?",
            (relative_path,),
        ).fetchone()
        if row is None:
            return None
        return FileState(
            relative_path=row["relative_path"],
            content_hash=row["content_hash"],
            local_mtime=row["local_mtime"],
            last_synced_etag=row["last_synced_etag"],
            last_synced_at=row["last_synced_at"],
            is_deleted=bool(row["is_deleted"]),
        )

    def upsert(self, state: FileState) -> None:
        self._conn.execute(
            """INSERT INTO file_state
               (relative_path, content_hash, local_mtime, last_synced_etag, last_synced_at, is_deleted)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(relative_path) DO UPDATE SET
                 content_hash = excluded.content_hash,
                 local_mtime = excluded.local_mtime,
                 last_synced_etag = excluded.last_synced_etag,
                 last_synced_at = excluded.last_synced_at,
                 is_deleted = excluded.is_deleted
            """,
            (
                state.relative_path,
                state.content_hash,
                state.local_mtime,
                state.last_synced_etag,
                state.last_synced_at,
                int(state.is_deleted),
            ),
        )
        self._conn.commit()

    def get_all(self) -> list[FileState]:
        rows = self._conn.execute("SELECT * FROM file_state").fetchall()
        return [
            FileState(
                relative_path=row["relative_path"],
                content_hash=row["content_hash"],
                local_mtime=row["local_mtime"],
                last_synced_etag=row["last_synced_etag"],
                last_synced_at=row["last_synced_at"],
                is_deleted=bool(row["is_deleted"]),
            )
            for row in rows
        ]

    def delete(self, relative_path: str) -> None:
        self._conn.execute(
            "DELETE FROM file_state WHERE relative_path = ?",
            (relative_path,),
        )
        self._conn.commit()

    def add_pending_delete(
        self, relative_path: str, deleted_at: str, propagate_after: str
    ) -> None:
        self._conn.execute(
            """INSERT INTO pending_deletes (relative_path, deleted_at, propagate_after)
               VALUES (?, ?, ?)
               ON CONFLICT(relative_path) DO UPDATE SET
                 deleted_at = excluded.deleted_at,
                 propagate_after = excluded.propagate_after
            """,
            (relative_path, deleted_at, propagate_after),
        )
        self._conn.commit()

    def get_pending_deletes(self, before: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT relative_path FROM pending_deletes WHERE propagate_after <= ?",
            (before,),
        ).fetchall()
        return [row["relative_path"] for row in rows]

    def remove_pending_delete(self, relative_path: str) -> None:
        self._conn.execute(
            "DELETE FROM pending_deletes WHERE relative_path = ?",
            (relative_path,),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
