"""SQLite-backed dedup index.

Stores mapping of content sha256 to S3 key and Telegram file_unique_id to sha256.
Provides APIs to check duplicates and record new entries.
"""

from __future__ import annotations

import contextlib
import sqlite3
import time
from dataclasses import dataclass
from typing import Optional, Tuple

from ..runtime.config import AppConfig


SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
  sha256 TEXT PRIMARY KEY,
  s3_key TEXT NOT NULL,
  size_bytes INTEGER,
  mime TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS tg_map (
  file_unique_id TEXT PRIMARY KEY,
  sha256 TEXT NOT NULL,
  FOREIGN KEY(sha256) REFERENCES files(sha256)
);

CREATE TABLE IF NOT EXISTS messages (
  message_id TEXT,
  chat_id TEXT,
  media_group_id TEXT,
  created_at TEXT,
  PRIMARY KEY(message_id, chat_id)
);

CREATE INDEX IF NOT EXISTS idx_files_created_at ON files(created_at);
CREATE INDEX IF NOT EXISTS idx_tg_map_sha256 ON tg_map(sha256);
CREATE INDEX IF NOT EXISTS idx_messages_group ON messages(media_group_id);
-- Jobs for MTProto large-file processing
CREATE TABLE IF NOT EXISTS jobs (
  job_id TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL,
  chat_id INTEGER NOT NULL,
  message_id INTEGER NOT NULL,
  state TEXT NOT NULL,
  priority INTEGER NOT NULL DEFAULT 4,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_error TEXT,
  payload_json TEXT
);

CREATE TABLE IF NOT EXISTS job_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT NOT NULL,
  attempt INTEGER NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  success INTEGER,
  error TEXT,
  FOREIGN KEY(job_id) REFERENCES jobs(job_id)
);

CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state);
CREATE INDEX IF NOT EXISTS idx_job_attempts_job ON job_attempts(job_id);
-- Auth secrets for MTProto interactive login
CREATE TABLE IF NOT EXISTS auth_secrets (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""


@dataclass
class DuplicateResult:
    is_duplicate: bool
    existing_key: Optional[str]
    reason: Optional[str]


class DedupIndex:
    def __init__(self, config: AppConfig) -> None:
        self._path = config.sqlite_path
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._conn.close()

    def check_by_unique_id(self, file_unique_id: str) -> DuplicateResult:
        cur = self._conn.execute(
            "SELECT f.s3_key FROM tg_map t JOIN files f ON f.sha256=t.sha256 WHERE t.file_unique_id=?",
            (file_unique_id,),
        )
        row = cur.fetchone()
        if row:
            return DuplicateResult(True, row[0], "file_unique_id")
        return DuplicateResult(False, None, None)

    def check_by_sha256(self, sha256: str) -> DuplicateResult:
        cur = self._conn.execute("SELECT s3_key FROM files WHERE sha256=?", (sha256,))
        row = cur.fetchone()
        if row:
            return DuplicateResult(True, row[0], "sha256")
        return DuplicateResult(False, None, None)

    def record(
        self,
        sha256: str,
        s3_key: str,
        size_bytes: int,
        mime: Optional[str],
        file_unique_id: Optional[str] = None,
    ) -> None:
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self._conn.execute(
            "INSERT OR IGNORE INTO files(sha256, s3_key, size_bytes, mime, created_at) VALUES(?,?,?,?,?)",
            (sha256, s3_key, size_bytes, mime, now_iso),
        )
        if file_unique_id:
            self._conn.execute(
                "INSERT OR IGNORE INTO tg_map(file_unique_id, sha256) VALUES(?,?)",
                (file_unique_id, sha256),
            )
        self._conn.commit()

    def vacuum(self) -> None:
        self._conn.execute("VACUUM")
        self._conn.commit()

    # --- Job store helpers (minimal for Phase 1) ---

    def upsert_job(self, job_id: str, user_id: int, chat_id: int, message_id: int, state: str, priority: int, now_iso: str, payload_json: Optional[str] = None) -> None:
        """Insert or update a job row.

        Parameters:
        - job_id: str - unique job identifier (UUIDv4)
        - user_id: int - Telegram user id
        - chat_id: int - Telegram chat id
        - message_id: int - Telegram message id
        - state: str - job state (PENDING|PROCESSING|COMPLETED|FAILED|RETRYING|CANCELLED)
        - priority: int - priority 0..9
        - now_iso: str - ISO8601 UTC timestamp used for created/updated
        """
        self._conn.execute(
            """
INSERT INTO jobs(job_id, user_id, chat_id, message_id, state, priority,
                 created_at, updated_at, payload_json)
VALUES(?,?,?,?,?,?,?,?,?)
ON CONFLICT(job_id) DO UPDATE SET state=excluded.state,
  priority=excluded.priority,
  updated_at=excluded.updated_at,
  payload_json=COALESCE(excluded.payload_json, jobs.payload_json)
""",
            (
                job_id,
                user_id,
                chat_id,
                message_id,
                state,
                int(priority),
                now_iso,
                now_iso,
                payload_json,
            ),
        )
        self._conn.commit()

    def update_job_state(self, job_id: str, state: str, last_error: Optional[str], now_iso: str) -> None:
        """Update the job state and optional last_error.

        Parameters:
        - job_id: str - job identifier
        - state: str - new state
        - last_error: Optional[str] - error text if failed
        - now_iso: str - ISO8601 UTC timestamp for updated_at
        """
        self._conn.execute(
            "UPDATE jobs SET state=?, last_error=?, updated_at=? WHERE job_id=?",
            (state, last_error, now_iso, job_id),
        )
        self._conn.commit()

    def get_job(self, job_id: str) -> Optional[Tuple[str, int, int, int, str, int, str, str, Optional[str], Optional[str]]]:
        """Fetch a job row by id.

        Returns a tuple with columns corresponding to the `jobs` table.
        """
        cur = self._conn.execute(
            "SELECT job_id, user_id, chat_id, message_id, state, priority, created_at, updated_at, last_error, payload_json FROM jobs WHERE job_id=?",
            (job_id,),
        )
        return cur.fetchone()

    def list_jobs(self, limit: int = 20) -> list[Tuple[str, int, int, int, str, int, str, str, Optional[str]]]:
        """List recent jobs ordered by updated_at descending."""
        cur = self._conn.execute(
            "SELECT job_id, user_id, chat_id, message_id, state, priority, created_at, updated_at, last_error FROM jobs ORDER BY updated_at DESC LIMIT ?",
            (int(limit),),
        )
        return cur.fetchall()

    # --- Auth secrets (MTProto code/password exchange) ---

    def set_secret(self, key: str, value: str, now_iso: str) -> None:
        """Store or replace a secret value (e.g., mt_code, mt_password).

        Parameters:
        - key: str - secret key name
        - value: str - secret value
        - now_iso: str - timestamp in ISO8601 UTC
        """
        self._conn.execute(
            "REPLACE INTO auth_secrets(key, value, created_at) VALUES(?,?,?)",
            (key, value, now_iso),
        )
        self._conn.commit()

    def get_secret_since(self, key: str, since_iso: str) -> Optional[Tuple[str, str]]:
        """Return (value, created_at) if secret exists and is newer than since_iso."""
        cur = self._conn.execute(
            "SELECT value, created_at FROM auth_secrets WHERE key=? AND created_at>=?",
            (key, since_iso),
        )
        row = cur.fetchone()
        if not row:
            return None
        return row[0], row[1]

    def delete_secret(self, key: str) -> None:
        """Delete a secret entry by key."""
        self._conn.execute("DELETE FROM auth_secrets WHERE key=?", (key,))
        self._conn.commit()

    def purge_all(self) -> dict[str, int]:
        """Purge all data from the database and return counts of deleted records.
        
        This is a destructive operation that removes ALL data from the database.
        Use with extreme caution and only for debugging/security purposes.
        
        Returns:
        - dict[str, int]: Counts of deleted records by table
        """
        counts = {}
        
        # Count and delete from each table
        tables = ['files', 'jobs', 'auth_secrets']
        
        for table in tables:
            # Get count before deletion
            cur = self._conn.execute(f"SELECT COUNT(*) FROM {table}")
            count = cur.fetchone()[0]
            
            # Delete all records
            self._conn.execute(f"DELETE FROM {table}")
            
            counts[table] = count
        
        # Commit all deletions
        self._conn.commit()
        
        # Reset auto-increment counters if they exist
        try:
            self._conn.execute("DELETE FROM sqlite_sequence")
            self._conn.commit()
        except Exception:
            # sqlite_sequence table might not exist, ignore
            pass
        
        return counts

