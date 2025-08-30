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

