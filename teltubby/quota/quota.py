"""Quota tracking for MinIO bucket.

Implements a pragmatic strategy:
- If `S3_BUCKET_QUOTA_BYTES` is set, compute used ratio by summing object sizes
  under the bucket (cached) and dividing by quota.
- Otherwise, returns None to indicate unknown (ack will state unknown).
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

from ..runtime.config import AppConfig
from ..storage.s3_client import S3Client
from ..metrics.registry import MINIO_BUCKET_USED_RATIO


logger = logging.getLogger("teltubby.quota")


class QuotaManager:
    def __init__(self, cfg: AppConfig, s3: S3Client) -> None:
        self._cfg = cfg
        self._s3 = s3
        self._quota = int(os.getenv("S3_BUCKET_QUOTA_BYTES", "0") or 0) or None
        self._last_used_bytes = 0
        self._last_refresh = 0.0

    def refresh_used_bytes(self, cache_ttl_seconds: int = 300) -> int:
        now = time.time()
        if now - self._last_refresh < cache_ttl_seconds and self._last_used_bytes > 0:
            return self._last_used_bytes
        # Sum all objects in bucket (could be expensive on large buckets)
        total = 0
        try:
            for obj in self._s3._client.list_objects(self._s3._bucket, recursive=True):
                total += getattr(obj, "size", 0)
        except Exception as e:
            logger.warning("failed to list bucket for quota", exc_info=e)
        self._last_used_bytes = total
        self._last_refresh = now
        return total

    def used_ratio(self) -> Optional[float]:
        if not self._quota:
            return None
        used = self.refresh_used_bytes()
        ratio = min(1.0, used / float(self._quota))
        MINIO_BUCKET_USED_RATIO.set(ratio)
        return ratio

