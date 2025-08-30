"""S3/MinIO client wrapper.

Provides an async-friendly wrapper around the MinIO SDK for uploads with
multipart support, private ACL, and path-style access as needed for MinIO.
"""

from __future__ import annotations

import hashlib
import io
import os
from typing import BinaryIO, Optional

from minio import Minio
from minio.commonconfig import Tags
from minio.deleteobjects import DeleteObject

from ..runtime.config import AppConfig


class S3Client:
    """Thin client over minio.Minio tailored to our config and needs."""

    def __init__(self, config: AppConfig) -> None:
        secure = not config.minio_tls_skip_verify and config.s3_endpoint.startswith("https")
        # The Minio client treats verify via certs installed in system store.
        self._client = Minio(
            endpoint=config.s3_endpoint.replace("https://", "").replace("http://", ""),
            access_key=config.s3_access_key_id,
            secret_key=config.s3_secret_access_key,
            secure=secure,
        )
        self._bucket = config.s3_bucket

    def ensure_bucket(self) -> None:
        if not self._client.bucket_exists(self._bucket):
            self._client.make_bucket(self._bucket)

    def upload_fileobj(
        self,
        key: str,
        fileobj: BinaryIO,
        length: int,
        content_type: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> None:
        self._client.put_object(
            bucket_name=self._bucket,
            object_name=key,
            data=fileobj,
            length=length,
            content_type=content_type,
            metadata=metadata,
        )

    def stat(self, key: str):  # type: ignore[no-untyped-def]
        return self._client.stat_object(self._bucket, key)

    def delete(self, key: str) -> None:
        self._client.remove_object(self._bucket, key)

    def get_presigned_url(self, key: str, expires_seconds: int = 3600) -> str:
        return self._client.presigned_get_object(self._bucket, key, expires=expires_seconds)

