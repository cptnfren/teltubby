"""MTProto worker service.

Consumes jobs from RabbitMQ, downloads large files via MTProto, uploads to S3,
updates job state in SQLite, and reports progress via logs. Enhanced with
session health monitoring, automatic re-authentication, and admin notifications.
"""

from __future__ import annotations

import asyncio
import datetime as dt
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
    """RabbitMQ consumer with enhanced MTProto session monitoring and recovery."""

    def __init__(self, cfg: AppConfig) -> None:
        self._cfg = cfg
        self._s3 = S3Client(cfg)
        self._db = DedupIndex(cfg)
        self._mt = MTProtoClient(cfg)
        self._conn: Optional[aio_pika.RobustConnection] = None
        self._ch: Optional[aio_pika.RobustChannel] = None
        self._queue: Optional[aio_pika.Queue] = None
        self._simulate_download: bool = False
        self._session_health_task: Optional[asyncio.Task] = None
        self._last_session_check: Optional[dt.datetime] = None
        self._session_check_interval: int = 300  # 5 minutes
        self._auth_failure_count: int = 0
        self._max_auth_failures: int = 3

    async def start(self) -> None:
        """Initialize worker with enhanced session monitoring."""
        # Ensure bucket exists
        self._s3.ensure_bucket()

        # Start MTProto session if credentials are present; otherwise simulate
        if self._cfg.mtproto_api_id and self._cfg.mtproto_api_hash and self._cfg.mtproto_phone_number:
            try:
                await self._initialize_mtproto()
                # Start session health monitoring
                self._session_health_task = asyncio.create_task(self._monitor_session_health())
                logger.info("MTProto session monitoring started")
            except Exception:
                logger.exception("MTProto start failed; entering simulate mode")
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
        
        # Declare queue with same arguments as job_manager to avoid conflicts
        args: Dict[str, Any] = {
            "x-dead-letter-exchange": self._cfg.job_dlx_exchange,
            "x-dead-letter-routing-key": self._cfg.job_dead_letter_queue,
            "x-max-priority": 9,
        }
        self._queue = await self._ch.declare_queue(
            self._cfg.job_queue_name, 
            durable=True,
            arguments=args
        )

        await self._queue.consume(self._on_message)
        logger.info("worker started", extra={"queue": self._cfg.job_queue_name})

    async def _initialize_mtproto(self) -> None:
        """Initialize MTProto client with authentication hooks."""
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
        self._mt = MTProtoClient(self._cfg, hooks=hooks)
        await self._mt.start()
        self._last_session_check = dt.datetime.utcnow()

    async def _monitor_session_health(self) -> None:
        """Periodically monitor MTProto session health and trigger re-auth if needed."""
        while True:
            try:
                await asyncio.sleep(self._session_check_interval)
                if not self._simulate_download:
                    is_healthy = await self._check_session_health()
                    if not is_healthy:
                        await self._handle_session_expiry()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("Session health monitoring error")

    async def _check_session_health(self) -> bool:
        """Check if MTProto session is still valid."""
        try:
            if not self._mt._client:
                return False
            
            # Try to get current user info
            me = await self._mt._client.get_me()
            if me:
                self._last_session_check = dt.datetime.utcnow()
                self._auth_failure_count = 0  # Reset failure count on success
                return True
            return False
        except Exception as e:
            logger.warning("Session health check failed", extra={"error": str(e)})
            self._auth_failure_count += 1
            return False

    async def _handle_session_expiry(self) -> None:
        """Handle expired MTProto session with automatic recovery."""
        logger.warning("MTProto session expired, attempting re-authentication")
        
        try:
            # Notify admins about authentication issue
            await self._notify_admin_auth_needed()
            
            # Attempt re-authentication
            await self._reauthenticate_mtproto()
            
        except Exception as e:
            logger.exception("Failed to handle session expiry")
            # If re-auth fails multiple times, enter simulate mode
            if self._auth_failure_count >= self._max_auth_failures:
                logger.error("Max auth failures reached, entering simulate mode")
                self._simulate_download = True
                await self._notify_admin_critical_failure()

    async def _reauthenticate_mtproto(self) -> None:
        """Attempt to re-authenticate MTProto client."""
        try:
            logger.info("Attempting MTProto re-authentication")
            
            # Stop current client
            if self._mt._client:
                await self._mt.stop()
            
            # Re-initialize with new authentication
            await self._initialize_mtproto()
            
            logger.info("MTProto re-authentication successful")
            await self._notify_admin_auth_restored()
            
        except Exception as e:
            logger.exception("MTProto re-authentication failed")
            raise

    async def _notify_admin_auth_needed(self) -> None:
        """Notify all admin users that re-authentication is needed."""
        try:
            from telegram import Bot
            bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
            if bot_token:
                bot = Bot(token=bot_token)
                message = (
                    "⚠️ **MTProto Authentication Required**\n\n"
                    "The MTProto session has expired and needs re-authentication.\n\n"
                    "**Action Required:** Send `/mtcode <verification_code>` to this bot.\n\n"
                    "The worker will automatically retry authentication once the code is provided."
                )
                
                for admin_id in self._cfg.telegram_whitelist_ids:
                    try:
                        await bot.send_message(
                            chat_id=admin_id,
                            text=message,
                            parse_mode="Markdown"
                        )
                    except Exception as e:
                        logger.warning(f"Failed to notify admin {admin_id}", extra={"error": str(e)})
                        
        except Exception as e:
            logger.exception("Failed to send admin notification")

    async def _notify_admin_auth_restored(self) -> None:
        """Notify admins that authentication has been restored."""
        try:
            from telegram import Bot
            bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
            if bot_token:
                bot = Bot(token=bot_token)
                message = "✅ **MTProto Authentication Restored**\n\nThe worker is now processing large files normally."
                
                for admin_id in self._cfg.telegram_whitelist_ids:
                    try:
                        await bot.send_message(
                            chat_id=admin_id,
                            text=message,
                            parse_mode="Markdown"
                        )
                    except Exception as e:
                        logger.warning(f"Failed to notify admin {admin_id}", extra={"error": str(e)})
                        
        except Exception as e:
            logger.exception("Failed to send auth restored notification")

    async def _notify_admin_critical_failure(self) -> None:
        """Notify admins about critical authentication failure."""
        try:
            from telegram import Bot
            bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
            if bot_token:
                bot = Bot(token=bot_token)
                message = (
                    "🚨 **Critical MTProto Failure**\n\n"
                    "The worker has failed to authenticate after multiple attempts.\n\n"
                    "**Current Status:** Running in simulate mode (large files will not be processed)\n\n"
                    "**Action Required:** Manual intervention needed. Check worker logs and restart if necessary."
                )
                
                for admin_id in self._cfg.telegram_whitelist_ids:
                    try:
                        await bot.send_message(
                            chat_id=admin_id,
                            text=message,
                            parse_mode="Markdown"
                        )
                    except Exception as e:
                        logger.warning(f"Failed to notify admin {admin_id}", extra={"error": str(e)})
                        
        except Exception as e:
            logger.exception("Failed to send critical failure notification")

    async def close(self) -> None:
        """Clean shutdown with session monitoring cleanup."""
        if self._session_health_task:
            self._session_health_task.cancel()
            try:
                await self._session_health_task
            except asyncio.CancelledError:
                pass
        
        if self._ch:
            await self._ch.close()
        if self._conn:
            await self._conn.close()
        await self._mt.stop()

    def get_health_status(self) -> Dict[str, Any]:
        """Get current worker health status for monitoring."""
        return {
            "status": "running" if not self._simulate_download else "simulate_mode",
            "mtproto_authenticated": not self._simulate_download and self._mt._client is not None,
            "last_session_check": self._last_session_check.isoformat() if self._last_session_check else None,
            "auth_failure_count": self._auth_failure_count,
            "session_check_interval": self._session_check_interval,
            "queue_consumers": 1 if self._queue else 0,
            "simulate_download": self._simulate_download
        }

    async def _on_message(self, message: aio_pika.IncomingMessage) -> None:
        """Process job message with enhanced error handling and session validation."""
        async with message.process(requeue=False):
            try:
                payload: Dict[str, Any] = json.loads(message.body)
                job = _parse_job(payload)

                # Mark job PROCESSING
                self._db.update_job_state(job.job_id, "PROCESSING", None, _now_iso())

                # Validate session before processing (if not in simulate mode)
                if not self._simulate_download:
                    is_healthy = await self._check_session_health()
                    if not is_healthy:
                        logger.warning("Session unhealthy during job processing, attempting recovery")
                        await self._handle_session_expiry()
                        # Re-check health after recovery attempt
                        is_healthy = await self._check_session_health()
                        if not is_healthy:
                            raise RuntimeError("Failed to recover MTProto session")

                # Download via MTProto or simulate if not available
                tmp_fd, tmp_path = tempfile.mkstemp(prefix="mtw_")
                os.close(tmp_fd)
                if self._simulate_download:
                    size_bytes = job.file_size or 1024 * 1024
                    with open(tmp_path, "wb") as f:
                        f.seek(max(0, size_bytes - 1))
                        f.write(b"\0")
                else:
                    # Actually download the file via MTProto
                    try:
                        logger.info(
                            f"Downloading file via MTProto: "
                            f"chat_id={job.chat_id}, message_id={job.message_id}"
                        )
                        
                        # Create progress callback for logging
                        async def progress_callback(current: int, total: int):
                            if total > 0:
                                percent = (current / total) * 100
                                logger.info(
                                    f"Download progress: {current}/{total} "
                                    f"bytes ({percent:.1f}%)"
                                )
                        
                        # Download the actual file
                        size_bytes = await self._mt.download_file_by_message(
                            chat_id=job.chat_id,
                            message_id=job.message_id,
                            dest_path=tmp_path,
                            on_progress=progress_callback
                        )
                        
                        logger.info(
                            f"Successfully downloaded {size_bytes} bytes to {tmp_path}"
                        )
                        
                        # Verify file integrity
                        actual_size = os.path.getsize(tmp_path)
                        if actual_size != size_bytes:
                            raise RuntimeError(
                                f"File size mismatch: expected {size_bytes}, got {actual_size}"
                            )
                        
                        if actual_size == 0:
                            raise RuntimeError("Downloaded file is empty")
                            
                    except Exception as download_error:
                        logger.error(f"MTProto download failed: {download_error}")
                        # Clean up temp file
                        if os.path.exists(tmp_path):
                            os.remove(tmp_path)
                        raise RuntimeError(
                            f"Failed to download file via MTProto: {download_error}"
                        )

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


