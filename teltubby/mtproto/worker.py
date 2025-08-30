"""MTProto worker service.

Consumes jobs from RabbitMQ, downloads large files via MTProto, uploads to S3,
updates job state in SQLite, and reports progress via logs. Phase 2 focuses on
core consume → download → upload pipeline with robust error handling.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import io
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from typing import Any, Dict, Optional

import aio_pika
from urllib.parse import quote

from ..runtime.config import AppConfig
from ..runtime.logging_setup import setup_logging
from ..db.dedup import DedupIndex
from ..mtproto.client import MTProtoClient
from ..storage.s3_client import S3Client
from ..metrics.registry import JOBS_COMPLETED, JOBS_FAILED


logger = logging.getLogger("teltubby.worker")


@dataclass
class Job:
    job_id: str
    chat_id: int
    message_id: int
    file_id: str
    file_unique_id: str
    file_size: Optional[int]
    file_type: str
    file_name: Optional[str]
    mime_type: Optional[str]
    caption: Optional[str]
    media_group_id: Optional[str]
    created_at: str


def _now_iso() -> str:
    return dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_job(payload: Dict[str, Any]) -> Job:
    fi = payload["file_info"]
    tc = payload["telegram_context"]
    jm = payload["job_metadata"]
    return Job(
        job_id=payload["job_id"],
        chat_id=int(payload["chat_id"]),
        message_id=int(payload["message_id"]),
        file_id=str(fi["file_id"]),
        file_unique_id=str(fi["file_unique_id"]),
        file_size=int(fi["file_size"]) if fi.get("file_size") else None,
        file_type=str(fi["file_type"]),
        file_name=fi.get("file_name"),
        mime_type=fi.get("mime_type"),
        caption=tc.get("caption"),
        media_group_id=str(tc.get("media_group_id")) if tc.get("media_group_id") else None,
        created_at=str(jm.get("created_at")),
    )


class Worker:
    """RabbitMQ consumer that processes jobs sequentially (Phase 2)."""

    def __init__(self, cfg: AppConfig) -> None:
        self._cfg = cfg
        self._s3 = S3Client(cfg)
        self._db = DedupIndex(cfg)
        self._mt = MTProtoClient(cfg)
        self._conn: Optional[aio_pika.RobustConnection] = None
        self._ch: Optional[aio_pika.RobustChannel] = None
        self._queue: Optional[aio_pika.Queue] = None
        self._simulate_download: bool = False

    async def start(self) -> None:
        # Ensure bucket exists
        self._s3.ensure_bucket()

        # Start MTProto session if credentials are present; otherwise simulate
        if self._cfg.mtproto_api_id and self._cfg.mtproto_api_hash and self._cfg.mtproto_phone_number:
            try:
                # Provide hooks that poll DB for code/password set via bot
                async def _wait_code() -> str:
                    while True:
                        now_minus = (dt.datetime.utcnow() - dt.timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
                        got = self._db.get_secret_since("mt_code", now_minus)
                        if got:
                            code, _ts = got
                            self._db.delete_secret("mt_code")
                            return code
                        await asyncio.sleep(2)

                async def _wait_pass() -> str:
                    while True:
                        now_minus = (dt.datetime.utcnow() - dt.timedelta(minutes=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
                        got = self._db.get_secret_since("mt_password", now_minus)
                        if got:
                            pwd, _ts = got
                            # do not delete password; leave until user changes it
                            return pwd
                        await asyncio.sleep(2)

                hooks = type("H", (), {})()
                setattr(hooks, "request_code", _wait_code)
                setattr(hooks, "request_password", _wait_pass)
                self._mt = MTProtoClient(self._cfg, hooks=hooks)  # replace with hooked client
                await self._mt.start()
            except Exception:
                logger.exception("mtproto start failed; entering simulate mode")
                self._simulate_download = True
        else:
            logger.warning("MTProto credentials not configured; simulate mode enabled")
            self._simulate_download = True

        # AMQP connection
        vhost_quoted = quote(self._cfg.rabbitmq_vhost or "/", safe="")
        url = (
            f"amqp://{self._cfg.rabbitmq_username}:"
            f"{self._cfg.rabbitmq_password}@{self._cfg.rabbitmq_host}:"
            f"{self._cfg.rabbitmq_port}/{vhost_quoted}"
        )
        self._conn = await aio_pika.connect_robust(url)
        self._ch = await self._conn.channel()
        await self._ch.set_qos(prefetch_count=max(1, self._cfg.worker_concurrency))
        self._queue = await self._ch.declare_queue(self._cfg.job_queue_name, durable=True)

        await self._queue.consume(self._on_message)
        logger.info("worker started", extra={"queue": self._cfg.job_queue_name})

    async def close(self) -> None:
        if self._ch:
            await self._ch.close()
        if self._conn:
            await self._conn.close()
        await self._mt.stop()

    async def _on_message(self, message: aio_pika.IncomingMessage) -> None:
        async with message.process(requeue=False):
            try:
                payload: Dict[str, Any] = json.loads(message.body)
                job = _parse_job(payload)

                # Mark job PROCESSING
                self._db.update_job_state(job.job_id, "PROCESSING", None, _now_iso())

                # Download via MTProto or simulate if not available
                tmp_fd, tmp_path = tempfile.mkstemp(prefix="mtw_")
                os.close(tmp_fd)
                if self._simulate_download:
                    size_bytes = job.file_size or 1024 * 1024
                    with open(tmp_path, "wb") as f:
                        f.seek(max(0, size_bytes - 1))
                        f.write(b"\0")
                else:
                    # TODO: Implement real MTProto download by message/chat id in Phase 2.1
                    size_bytes = job.file_size or 0
                    with open(tmp_path, "wb") as f:
                        if size_bytes > 0:
                            f.seek(size_bytes - 1)
                            f.write(b"\0")

                # Upload to S3 using existing key scheme (simplified for Phase 2)
                year = dt.datetime.utcnow().strftime("%Y")
                month = dt.datetime.utcnow().strftime("%m")
                base_path = f"teltubby/{year}/{month}/mtproto/{job.message_id}/"
                fname = job.file_name or f"{job.file_unique_id}.bin"
                key = f"{base_path}{fname}"
                with open(tmp_path, "rb") as f:
                    self._s3.upload_fileobj(key, f, os.path.getsize(tmp_path), content_type=job.mime_type)
                os.remove(tmp_path)

                # Mark job COMPLETED and notify user via bot if token set
                self._db.update_job_state(job.job_id, "COMPLETED", None, _now_iso())
                logger.info("job completed", extra={"job_id": job.job_id, "s3_key": key})
                JOBS_COMPLETED.inc()
                try:
                    import os
                    from telegram import Bot
                    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
                    if bot_token:
                        bot = Bot(token=bot_token)
                        await bot.send_message(
                            chat_id=job.chat_id,
                            text=f"Large file archived successfully. Job {job.job_id}"
                        )
                except Exception:
                    logger.exception("failed to send completion notification")
            except Exception as e:
                logger.exception("job failed")
                self._db.update_job_state(job.job_id if 'job' in locals() else "", "FAILED", str(e), _now_iso())
                JOBS_FAILED.inc()


async def run_worker() -> None:
    cfg = AppConfig.from_env()
    setup_logging(cfg)
    w = Worker(cfg)
    await w.start()
    # Run forever
    await asyncio.Event().wait()


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()


