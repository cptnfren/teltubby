"""Telegram bot service for teltubby.

Provides whitelist enforcement, DM-only handling, mode selection (polling/webhook),
basic command handlers (/start, /help, /status, /quota, /mode, /db_maint), and a
placeholder ingestion pipeline to be expanded in subsequent edits.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from telegram import Update
from telegram.constants import ParseMode, ChatAction
from telegram.ext import (
    AIORateLimiter,
    Application,
    ApplicationBuilder,
    CallbackContext,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
# ChatAction is already imported from telegram.constants above
from ..runtime.config import AppConfig
from ..storage.s3_client import S3Client
from ..db.dedup import DedupIndex
from ..ingest.album_aggregator import AlbumAggregator
from ..ingest.pipeline import process_batch
from ..quota.quota import QuotaManager
from ..utils.telemetry_formatter import TelemetryFormatter, TelemetryData
from ..queue.job_manager import JobManager


logger = logging.getLogger("teltubby.bot")


def _is_whitelisted(user_id: Optional[int], cfg: AppConfig) -> bool:
    return user_id is not None and user_id in cfg.telegram_whitelist_ids


class TeltubbyBotService:
    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._app: Optional[Application] = None
        self._s3: Optional[S3Client] = None
        self._dedup: Optional[DedupIndex] = None
        self._albums = AlbumAggregator(
            window_seconds=config.album_aggregation_window_seconds
        )
        self._quota: Optional[QuotaManager] = None
        self._finalizer_task: Optional[asyncio.Task] = None
        self._jobs: Optional[JobManager] = None

    async def start(self) -> None:
        builder = (
            ApplicationBuilder()
            .token(self._config.telegram_bot_token)
            .rate_limiter(AIORateLimiter())
        )
        self._app = builder.build()

        # Commands - must be added BEFORE message handlers
        self._app.add_handler(CommandHandler("start", self._cmd_start))
        self._app.add_handler(CommandHandler("help", self._cmd_help))
        self._app.add_handler(CommandHandler("status", self._cmd_status))
        self._app.add_handler(CommandHandler("quota", self._cmd_quota))
        self._app.add_handler(CommandHandler("mode", self._cmd_mode))
        self._app.add_handler(CommandHandler("db_maint", self._cmd_db_maint))
        # MTProto interactive auth
        self._app.add_handler(CommandHandler("mtcode", self._cmd_mtcode))
        self._app.add_handler(CommandHandler("mtpass", self._cmd_mtpass))
        # MTProto worker monitoring
        self._app.add_handler(CommandHandler("mtstatus", self._cmd_mtstatus))
        # Queue/Job management commands (admin-only: enforced by whitelist)
        self._app.add_handler(CommandHandler("queue", self._cmd_queue))
        self._app.add_handler(CommandHandler("jobs", self._cmd_jobs))
        self._app.add_handler(CommandHandler("retry", self._cmd_retry))
        self._app.add_handler(CommandHandler("cancel", self._cmd_cancel))
        self._app.add_handler(CommandHandler("purge", self._cmd_purge))

        # Ingestion: only messages with media content in DMs
        self._app.add_handler(
            MessageHandler(filters.ALL & filters.ChatType.PRIVATE, self._on_message),
        )

        # Initialize support services
        self._s3 = S3Client(self._config)
        self._dedup = DedupIndex(self._config)
        self._quota = QuotaManager(self._config, self._s3)
        # Initialize RabbitMQ job manager
        self._jobs = JobManager(self._config)
        await self._jobs.initialize()

        # Lifecycle per PTB v21
        await self._app.initialize()
        await self._app.start()
        # Start periodic finalizer
        self._finalizer_task = asyncio.create_task(self._finalizer_loop())
        if self._config.telegram_mode == "webhook":
            await self._app.updater.start_webhook(listen="0.0.0.0", port=8080)
            if self._config.webhook_url:
                await self._app.bot.set_webhook(url=self._config.webhook_url)
        else:
            await self._app.updater.start_polling(drop_pending_updates=True)

        logger.info("bot started", extra={"mode": self._config.telegram_mode})

    def _has_media_content(self, message) -> bool:
        """Check if message contains any media content."""
        return bool(
            message.photo or message.document or message.video or 
            message.audio or message.voice or message.animation or 
            message.sticker or message.video_note
        )

    def _typing_context(self, chat_id: int):
        """Context manager for showing typing indicator while processing."""
        class TypingContext:
            def __init__(self, bot, chat_id):
                self.bot = bot
                self.chat_id = chat_id
                self._typing_task = None
            
            async def __aenter__(self):
                """Start typing indicator."""
                if self.bot:
                    # Send an immediate typing action so the user sees it right away
                    try:
                        await self.bot.send_chat_action(
                            chat_id=self.chat_id,
                            action=ChatAction.TYPING,
                        )
                    except Exception:
                        pass
                    # Keep typing alive while processing
                    self._typing_task = asyncio.create_task(self._keep_typing())
                return self
            
            async def __aexit__(self, exc_type, exc_val, exc_tb):
                """Stop typing indicator."""
                if self._typing_task:
                    self._typing_task.cancel()
                    try:
                        await self._typing_task
                    except asyncio.CancelledError:
                        pass
            
            async def _keep_typing(self):
                """Keep typing indicator active while processing."""
                try:
                    while True:
                        await self.bot.send_chat_action(
                            chat_id=self.chat_id, 
                            action=ChatAction.TYPING
                        )
                        await asyncio.sleep(4)  # Telegram typing expires after ~5 seconds
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.warning(
                        "Failed to send typing indicator", 
                        extra={"error": str(e)}
                    )
        
        return TypingContext(self._app.bot if self._app else None, chat_id)

    async def stop(self) -> None:
        # Stop finalizer loop
        if self._finalizer_task:
            self._finalizer_task.cancel()
            try:
                await self._finalizer_task
            except Exception:
                pass
        if not self._app:
            return
        try:
            await self._app.updater.stop()
        except Exception:
            pass
        try:
            await self._app.stop()
        except Exception:
            pass
        try:
            await self._app.shutdown()
        except Exception:
            pass
        # Close job manager last
        try:
            if self._jobs:
                await self._jobs.close()
        except Exception:
            pass

    async def _finalizer_loop(self) -> None:
        """Periodically flush expired albums so ingestion continues.

        Runs every 1 second. If any albums are ready, process them in the
        same way as _on_message would after aggregation completes.
        """
        assert self._app and self._s3 and self._dedup
        logger.info("album finalizer loop started")
        try:
            while True:
                await asyncio.sleep(1)
                ready_batches = await self._albums.pop_ready_albums()
                for items in ready_batches:
                    try:
                        # Quota pause at 100%
                        if self._quota and self._config.bucket_quota_bytes:
                            ratio = self._quota.used_ratio()
                            if ratio is not None and ratio >= 1.0:
                                # Can't reply to a specific message here; skip processing
                                continue
                        
                        # Get last message for typing context and telemetry
                        last_msg = items[-1]
                        
                        logger.info(
                            f"Finalizer starting batch processing for {len(items)} items"
                        )
                        
                        # Show typing indicator while processing in finalizer
                        async with self._typing_context(last_msg.chat_id):
                            # Route over-bot-limit items to MTProto via queue before Bot API
                            to_process = []
                            queued_jobs = []
                            bot_limit = (
                                self._config.bot_api_max_file_size_bytes or (50 * 1024 * 1024)
                            )

                            async def _extract_file_info(m):
                                """Extract minimal file info for routing and job creation.
                                Returns dict with: file_id, file_unique_id, file_size, file_type,
                                file_name, mime_type
                                """
                                if m.photo:
                                    ph = max(
                                        m.photo or [],
                                        key=lambda p: (p.width or 0) * (p.height or 0),
                                    )
                                    return {
                                        "file_id": ph.file_id,
                                        "file_unique_id": ph.file_unique_id,
                                        "file_size": ph.file_size,
                                        "file_type": "photo",
                                        "file_name": None,
                                        "mime_type": "image/jpeg",
                                    }
                                if m.document:
                                    return {
                                        "file_id": m.document.file_id,
                                        "file_unique_id": m.document.file_unique_id,
                                        "file_size": m.document.file_size,
                                        "file_type": "document",
                                        "file_name": m.document.file_name,
                                        "mime_type": m.document.mime_type,
                                    }
                                if m.video:
                                    return {
                                        "file_id": m.video.file_id,
                                        "file_unique_id": m.video.file_unique_id,
                                        "file_size": m.video.file_size,
                                        "file_type": "video",
                                        "file_name": m.video.file_name,
                                        "mime_type": m.video.mime_type,
                                    }
                                if m.audio:
                                    return {
                                        "file_id": m.audio.file_id,
                                        "file_unique_id": m.audio.file_unique_id,
                                        "file_size": m.audio.file_size,
                                        "file_type": "audio",
                                        "file_name": m.audio.file_name,
                                        "mime_type": m.audio.mime_type,
                                    }
                                if m.voice:
                                    return {
                                        "file_id": m.voice.file_id,
                                        "file_unique_id": m.voice.file_unique_id,
                                        "file_size": m.voice.file_size,
                                        "file_type": "voice",
                                        "file_name": None,
                                        "mime_type": m.voice.mime_type,
                                    }
                                if m.animation:
                                    return {
                                        "file_id": m.animation.file_id,
                                        "file_unique_id": m.animation.file_unique_id,
                                        "file_size": m.animation.file_size,
                                        "file_type": "animation",
                                        "file_name": m.animation.file_name,
                                        "mime_type": m.animation.mime_type,
                                    }
                                if m.sticker:
                                    return {
                                        "file_id": m.sticker.file_id,
                                        "file_unique_id": m.sticker.file_unique_id,
                                        "file_size": m.sticker.file_size,
                                        "file_type": "sticker",
                                        "file_name": None,
                                        "mime_type": None,
                                    }
                                if m.video_note:
                                    return {
                                        "file_id": m.video_note.file_id,
                                        "file_unique_id": m.video_note.file_unique_id,
                                        "file_size": m.video_note.file_size,
                                        "file_type": "video_note",
                                        "file_name": None,
                                        "mime_type": None,
                                    }
                                return None

                            async def _check_file_size(finfo):
                                """Probe Bot API file accessibility; detect too-big files."""
                                try:
                                    tfile = await self._app.bot.get_file(finfo["file_id"])  # type: ignore[index]
                                    return False, getattr(tfile, "file_size", None)
                                except Exception as e:
                                    if "File is too big" in str(e):
                                        return True, None
                                    return False, finfo.get("file_size")

                            import datetime as _dt
                            import json as _json
                            assert self._jobs and self._dedup

                            for m in items:
                                finfo = await _extract_file_info(m)
                                if not finfo or finfo.get("file_id") is None:
                                    to_process.append(m)
                                    continue
                                is_too_big, actual_size = await _check_file_size(finfo)
                                size_hint = finfo.get("file_size") or actual_size or 0
                                if is_too_big or (size_hint and size_hint > bot_limit):
                                    job_id = self._jobs.new_job_id()
                                    created_at = _dt.datetime.utcnow().strftime(
                                        "%Y-%m-%dT%H:%M:%SZ"
                                    )
                                    user_id = (
                                        getattr(getattr(m, "from_user", None), "id", None) or 0
                                    )
                                    payload = {
                                        "job_id": job_id,
                                        "user_id": user_id,
                                        "chat_id": m.chat.id,
                                        "message_id": m.id,
                                        "file_info": {
                                            "file_id": finfo["file_id"],
                                            "file_unique_id": finfo["file_unique_id"],
                                            "file_size": size_hint,
                                            "file_type": finfo["file_type"],
                                            "file_name": finfo.get("file_name"),
                                            "mime_type": finfo.get("mime_type"),
                                        },
                                        "telegram_context": {
                                            "forward_origin": (
                                                m.forward_origin.to_dict() if m.forward_origin else None
                                            ),
                                            "caption": m.caption or None,
                                            "entities": [
                                                e.to_dict() for e in (m.entities or [])
                                            ],
                                            "media_group_id": m.media_group_id,
                                        },
                                        "job_metadata": {
                                            "created_at": created_at,
                                            "priority": "normal",
                                            "retry_count": 0,
                                            "max_retries": 3,
                                        },
                                    }
                                    await self._jobs.publish_job(payload)
                                    self._dedup.upsert_job(
                                        job_id,
                                        payload["user_id"],
                                        payload["chat_id"],
                                        payload["message_id"],
                                        "PENDING",
                                        4,
                                        created_at,
                                        _json.dumps(payload),
                                    )
                                    queued_jobs.append(job_id)
                                else:
                                    to_process.append(m)

                            # Acknowledge queued jobs (emoji-rich with one-click commands); suppress further responses
                            suppress_response = False
                            if queued_jobs:
                                suppress_response = True
                                await self._app.bot.send_message(
                                    chat_id=last_msg.chat_id,
                                    text=TelemetryFormatter.format_jobs_queued(queued_jobs),
                                    parse_mode=ParseMode.MARKDOWN,
                                )

                            # Process remaining items via Bot API
                            if to_process:
                                res = await process_batch(
                                    self._config,
                                    self._s3,
                                    self._dedup,
                                    self._app.bot,
                                    to_process,
                                )
                            else:
                                class _Dummy:
                                    outcomes = []
                                    base_path = ""
                                    total_bytes_uploaded = 0

                                res = _Dummy()
                        
                        logger.info(
                            "Finalizer processed batch successfully",
                            extra={
                                "message_ids": [m.message_id for m in items], 
                                "count": len(items)
                            },
                        )

                        # Check if the entire batch failed (all items skipped or failed)
                        successful_items = [
                            o for o in res.outcomes if o.s3_key and not o.skipped_reason
                        ]
                        failed_items = [o for o in res.outcomes if o.skipped_reason]
                        
                        if not queued_jobs and not successful_items and failed_items:
                            # All items failed - send failure message instead of success
                            # Extract factual failure reasons from the outcomes
                            failure_reasons = []
                            for outcome in failed_items:
                                if outcome.skipped_reason:
                                    if outcome.skipped_reason == "exceeds_bot_limit":
                                        size_str = (
                                            f"{outcome.size_bytes or 'unknown'} bytes" 
                                            if outcome.size_bytes else "unknown size"
                                        )
                                        failure_reasons.append(
                                            f"File {outcome.ordinal}: exceeds 50MB limit ({size_str})"
                                        )
                                    elif outcome.skipped_reason == "exceeds_cfg_limit":
                                        size_str = (
                                            f"{outcome.size_bytes or 'unknown'} bytes" 
                                            if outcome.size_bytes else "unknown size"
                                        )
                                        failure_reasons.append(
                                            f"File {outcome.ordinal}: exceeds configured limit ({size_str})"
                                        )
                                    elif outcome.skipped_reason == "download_failed":
                                        failure_reasons.append(
                                            f"File {outcome.ordinal}: download failed"
                                        )
                                    elif outcome.skipped_reason == "album_validation_failed":
                                        failure_reasons.append(
                                            f"File {outcome.ordinal}: validation failed"
                                        )
                                    elif outcome.skipped_reason == "no_media":
                                        failure_reasons.append(
                                            f"File {outcome.ordinal}: no media content"
                                        )
                                    else:
                                        failure_reasons.append(
                                            f"File {outcome.ordinal}: {outcome.skipped_reason}"
                                        )
                            
                            # Combine all failure reasons
                            if len(failure_reasons) > 1:
                                reason = "Album failures:\n• " + "\n• ".join(failure_reasons)
                            else:
                                reason = failure_reasons[0] if failure_reasons else "Unknown failure"
                            
                            text = TelemetryFormatter.format_ingestion_failed(
                                reason=reason,
                                item_count=len(items)
                            )
                            await self._app.bot.send_message(
                                chat_id=last_msg.chat_id,
                                text=text,
                                parse_mode=ParseMode.MARKDOWN
                            )
                        elif not queued_jobs:
                            # Some or all items succeeded - send success telemetry
                            try:
                                dedup_ordinals = [
                                    o.ordinal for o in res.outcomes if o.is_duplicate
                                ]
                                media_types = list({o.type for o in res.outcomes if o.s3_key})
                                skipped = [o for o in res.outcomes if o.skipped_reason]
                                
                                telemetry_data = TelemetryData(
                                    files_count=len(successful_items),
                                    media_types=media_types,
                                    base_path=res.base_path,
                                    dedup_count=len(dedup_ordinals),
                                    total_bytes=res.total_bytes_uploaded,
                                    skipped_count=len(skipped)
                                )
                                
                                ack = TelemetryFormatter.format_ingestion_ack(telemetry_data)
                                await self._app.bot.send_message(
                                    chat_id=last_msg.chat_id, 
                                    reply_to_message_id=last_msg.message_id, 
                                    text=ack,
                                    parse_mode=ParseMode.MARKDOWN
                                )
                            except Exception:
                                logger.exception("failed to send telemetry ack from finalizer")
                    except Exception as e:
                        logger.exception("finalizer ingestion failed")
                        # Send failure message to user
                        try:
                            error_str = str(e)
                            if "album_validation_failed" in error_str:
                                reason = "Album validation failed: " + error_str
                            elif "File is too big" in error_str:
                                reason = "Telegram API error: File is too big (exceeds 50MB limit)"
                            elif "download_failed" in error_str:
                                reason = "Download error: " + error_str
                            else:
                                reason = f"Processing error: {error_str}"
                            
                            text = TelemetryFormatter.format_ingestion_failed(
                                reason=reason, 
                                item_count=len(items)
                            )
                            await self._app.bot.send_message(
                                chat_id=last_msg.chat_id,
                                text=text,
                                parse_mode=ParseMode.MARKDOWN
                            )
                        except Exception:
                            logger.exception("failed to send failure message from finalizer")
        except asyncio.CancelledError:
            logger.info("album finalizer loop stopped")

    async def _cmd_start(self, update: Update, context: CallbackContext) -> None:
        if not _is_whitelisted(
            update.effective_user and update.effective_user.id, self._config
        ):
            return
        text = TelemetryFormatter.format_start()
        await update.effective_message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def _cmd_help(self, update: Update, context: CallbackContext) -> None:
        """Handle /help command with comprehensive command documentation."""
        if not _is_whitelisted(
            update.effective_user and update.effective_user.id, self._config
        ):
            return
        
        # Add debug logging to see if this function is being called
        logger.info("Help command executed", extra={"user_id": update.effective_user.id})
        
        help_text = """🤖 **Teltubby Bot - Complete Command Reference**

Welcome to Teltubby! This bot archives media files from Telegram conversations using both Bot API (≤50MB) and MTProto (≤2GB) for large files.

## 📋 **Basic Commands**

**`/start`** - Initialize the bot and show welcome message
**`/help`** - Show this comprehensive help message
**`/status`** - Show current bot status and system health
**`/quota`** - Display current storage usage and quota information
**`/mode`** - Show current operation mode (polling/webhook)

## 🔐 **MTProto Authentication Commands**

**`/mtcode <verification_code>`** - Submit Telegram verification code for MTProto authentication
**`/mtpass <password>`** - Submit 2FA password if your account has two-factor authentication enabled
**`/mtstatus`** - Check current MTProto worker status and authentication state

*Note: These commands are required to process files larger than 50MB via MTProto.*

## 📊 **Queue & Job Management Commands**

**`/queue`** - List recent jobs in the processing queue
**`/jobs <job_id>`** - Show detailed information for a specific job
**`/retry <job_id>`** - Retry a failed or cancelled job
**`/cancel <job_id>`** - Mark a job as cancelled (advisory only)

## 🛠️ **System Maintenance Commands**

**`/db_maint`** - Perform database maintenance (VACUUM operation)
**`/purge confirm`** - **DESTRUCTIVE**: Purge entire system (storage, database, queue)

## 📁 **How to Use**

### **For Regular Media Files (≤50MB):**
1. Simply send any media file (photo, video, document, etc.) to this bot
2. The bot will automatically process and archive it
3. You'll receive a confirmation message with storage details

### **For Large Files (>50MB):**
1. Send the large file to the bot
2. The bot will queue it for MTProto processing
3. You'll receive a "Queued for MTProto processing" message
4. If authentication is needed, use `/mtcode <code>` when prompted
5. Monitor progress with `/mtstatus`

### **Authentication Setup:**
1. When you first send a large file, the MTProto worker may need authentication
2. Telegram will send a verification code to your account
3. Use `/mtcode <code>` to submit the verification code
4. If you have 2FA enabled, also use `/mtpass <password>`
5. Check authentication status with `/mtstatus`

## 🔍 **Status Monitoring**

- **`/status`** - Overall system health and configuration
- **`/mtstatus`** - MTProto worker status and authentication state
- **`/quota`** - Storage usage and quota information
- **`/queue`** - Current job queue status

## 📝 **Examples**

```
/mtcode 123456          # Submit verification code
/mtpass mypassword      # Submit 2FA password
/mtstatus               # Check worker status
/jobs abc-123-def       # View job details
/retry abc-123-def      # Retry failed job
```

## 🆘 **Troubleshooting**

- **Worker not processing large files?** Use `/mtstatus` to check authentication
- **Need to re-authenticate?** Use `/mtcode` with the new verification code
- **Jobs failing?** Check `/queue` and use `/retry` for failed jobs
- **Storage full?** Check `/quota` for current usage

## ⚠️ **Dangerous Commands**

**`/purge confirm`** - This command will **PERMANENTLY DELETE** all data:
- All files in storage bucket
- All database records (files, jobs, authentication)
- All pending jobs in queue

**Use only for debugging or security purposes. This action cannot be undone!**

## 📞 **Support**

All commands are restricted to whitelisted users only. If you encounter issues, check the status commands first, then contact your system administrator.

---
*Teltubby - Professional Telegram Media Archival System*"""
        
        await update.effective_message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

    async def _cmd_mtcode(self, update: Update, context: CallbackContext) -> None:
        """Store MTProto login code sent by Telegram to the user account.

        Usage: /mtcode 12345
        """
        if not _is_whitelisted(
            update.effective_user and update.effective_user.id, self._config
        ):
            return
        if not self._dedup:
            return
        args = context.args if hasattr(context, "args") else []
        if not args:
            await update.effective_message.reply_text("Usage: /mtcode <code>")
            return
        code = args[0].strip()
        now_iso = __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime())
        self._dedup.set_secret("mt_code", code, now_iso)
        await update.effective_message.reply_text("MTProto code stored.")

    async def _cmd_mtpass(self, update: Update, context: CallbackContext) -> None:
        """Store MTProto 2FA password for the user account.

        Usage: /mtpass <password>
        """
        if not _is_whitelisted(
            update.effective_user and update.effective_user.id, self._config
        ):
            return
        if not self._dedup:
            return
        args = context.args if hasattr(context, "args") else []
        if not args:
            await update.effective_message.reply_text("Usage: /mtpass <password>")
            return
        pwd = args[0]
        now_iso = __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime())
        self._dedup.set_secret("mt_password", pwd, now_iso)
        await update.effective_message.reply_text("MTProto password stored.")

    async def _cmd_mtstatus(self, update: Update, context: CallbackContext) -> None:
        """Handle /mtstatus command for MTProto worker status."""
        if not _is_whitelisted(
            update.effective_user and update.effective_user.id, self._config
        ):
            return

        try:
            # Get worker status from Docker
            import subprocess
            
            # Check if worker container is running
            result = subprocess.run(
                ["docker", "ps", "--filter", "name=mtworker", "--format", "{{.Status}}"],
                capture_output=True, text=True, timeout=10
            )
            
            if result.returncode != 0:
                await update.effective_message.reply_text(
                    "❌ **Failed to query worker status.**\n\n"
                    "Docker command failed. Check if Docker is running.",
                    parse_mode=ParseMode.MARKDOWN
                )
                return
            
            container_status = result.stdout.strip()
            if not container_status:
                await update.effective_message.reply_text(
                    "⚠️ **Worker Status: Stopped**\n\n"
                    "The MTProto worker container is not running.\n"
                    "Large files will not be processed.",
                    parse_mode=ParseMode.MARKDOWN
                )
                return
            
            # Get recent worker logs for detailed status
            log_result = subprocess.run(
                ["docker", "logs", "mtworker", "--tail", "10"],
                capture_output=True, text=True, timeout=10
            )
            
            logs = log_result.stdout if log_result.returncode == 0 else ""
            
            # Parse status indicators
            status_indicators = []
            if "MTProto client started" in logs:
                status_indicators.append("✅ Authenticated with Telegram")
            if "worker started" in logs:
                status_indicators.append("✅ Worker running and consuming jobs")
            if "MTProto session monitoring started" in logs:
                status_indicators.append("✅ Session health monitoring active")
            if "simulate mode enabled" in logs:
                status_indicators.append("⚠️ Running in simulate mode")
            if "MTProto credentials not configured" in logs:
                status_indicators.append("❌ MTProto credentials missing")
            
            # Determine overall status
            if "simulate mode enabled" in logs:
                overall_status = "⚠️ **Simulate Mode**"
                status_desc = "Worker is running but not processing large files"
            elif "MTProto client started" in logs and "worker started" in logs:
                overall_status = "✅ **Healthy**"
                status_desc = "Worker is fully operational"
            elif "MTProto client started" in logs:
                overall_status = "🔄 **Authenticating**"
                status_desc = "Worker is starting up"
            else:
                overall_status = "❌ **Error**"
                status_desc = "Worker has encountered an error"
            
            # Format response
            response = f"{overall_status}\n\n{status_desc}\n\n"
            response += "**Container Status:** " + container_status + "\n\n"
            
            if status_indicators:
                response += "**Status Indicators:**\n"
                for indicator in status_indicators:
                    response += f"• {indicator}\n"
                response += "\n"
            
            # Add recent activity if available
            import re
            timestamp_match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', logs)
            if timestamp_match:
                response += f"**Last Activity:** {timestamp_match.group(1)}\n\n"
            
            response += "**Commands:**\n"
            response += "• `/mtcode <code>` - Submit verification code\n"
            response += "• `/mtpass <password>` - Submit 2FA password\n"
            response += "• `/mtstatus` - Check this status again\n"
            response += "• `/help` - View complete command reference"
            
            await update.effective_message.reply_text(response, parse_mode=ParseMode.MARKDOWN)
            
        except Exception as e:
            logger.exception("Failed to get MTProto worker status")
            await update.effective_message.reply_text(
                "❌ **Failed to get worker status.**\n\n"
                f"Error: {str(e)}\n\n"
                "Please try again or check the logs manually.",
                parse_mode=ParseMode.MARKDOWN
            )

    async def _cmd_status(self, update: Update, context: CallbackContext) -> None:
        if not _is_whitelisted(
            update.effective_user and update.effective_user.id, self._config
        ):
            return
        
        # Show typing indicator while checking status
        await update.effective_chat.send_action(ChatAction.TYPING)
        
        used_ratio = self._quota.used_ratio() if self._quota else None
        queue_depth = None
        try:
            if self._jobs:
                queue_depth = await self._jobs.get_queue_depth()
        except Exception:
            queue_depth = None
        text = TelemetryFormatter.format_status(self._config.telegram_mode, used_ratio)
        if queue_depth is not None:
            text = f"{text}\nQueue depth: {queue_depth}"
        await update.effective_message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def _cmd_quota(self, update: Update, context: CallbackContext) -> None:
        if not _is_whitelisted(
            update.effective_user and update.effective_user.id, self._config
        ):
            return
        if not self._quota:
            return
        
        # Show typing indicator while calculating quota
        await update.effective_chat.send_action(ChatAction.TYPING)
        
        used_ratio = self._quota.used_ratio()
        if used_ratio is None:
            await update.effective_message.reply_text(
                "Quota unknown (no bucket quota configured)."
            )
            return
        text = TelemetryFormatter.format_quota(used_ratio)
        await update.effective_message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def _cmd_mode(self, update: Update, context: CallbackContext) -> None:
        if not _is_whitelisted(
            update.effective_user and update.effective_user.id, self._config
        ):
            return
        text = TelemetryFormatter.format_mode(self._config.telegram_mode)
        await update.effective_message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def _cmd_db_maint(self, update: Update, context: CallbackContext) -> None:
        if not _is_whitelisted(
            update.effective_user and update.effective_user.id, self._config
        ):
            return
        if self._dedup:
            self._dedup.vacuum()
        text = TelemetryFormatter.format_db_maint()
        await update.effective_message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def _cmd_queue(self, update: Update, context: CallbackContext) -> None:
        """List recent jobs from the queue store.

        Variables:
        - update: Update - Telegram update object
        - context: CallbackContext - PTB context (unused)
        """
        if not _is_whitelisted(
            update.effective_user and update.effective_user.id, self._config
        ):
            return
        if not self._dedup:
            return
        rows = self._dedup.list_jobs(limit=20)
        if not rows:
            await update.effective_message.reply_text("Queue is empty.")
            return
        lines = [f"{TelemetryFormatter.EMOJIS['queue']} **Recent Jobs**"]
        for (job_id, user_id, chat_id, message_id, state, priority, created_at, updated_at, last_error) in rows:
            err = f" — {last_error[:60]}..." if last_error else ""
            lines.append(
                f"• `{job_id}` [{state}] prio={priority}{err}\n"
                f"  {TelemetryFormatter.EMOJIS['inspect']} /jobs {job_id}  "
                f"{TelemetryFormatter.EMOJIS['retry']} /retry {job_id}  "
                f"{TelemetryFormatter.EMOJIS['cancel']} /cancel {job_id}"
            )
        await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)

    async def _cmd_jobs(self, update: Update, context: CallbackContext) -> None:
        """Show details for a specific job id."""
        if not _is_whitelisted(
            update.effective_user and update.effective_user.id, self._config
        ):
            return
        if not self._dedup:
            return
        args = context.args if hasattr(context, "args") else []
        if not args:
            await update.effective_message.reply_text("Usage: /jobs <job_id>")
            return
        job_id = args[0]
        row = self._dedup.get_job(job_id)
        if not row:
            await update.effective_message.reply_text("Job not found.")
            return
        (job_id, user_id, chat_id, message_id, state, priority, created_at, updated_at, last_error, payload_json) = row
        text = (
            f"{TelemetryFormatter.EMOJIS['inspect']} **Job Details**\n\n"
            f"`{job_id}`\n"
            f"• State: {state}  • Priority: {priority}\n"
            f"• Chat: {chat_id}  • Msg: {message_id}\n"
            f"• Created: {created_at}\n"
            f"• Updated: {updated_at}\n"
            f"• Last Error: {last_error or '-'}\n\n"
            f"{TelemetryFormatter.EMOJIS['retry']} /retry {job_id}   {TelemetryFormatter.EMOJIS['cancel']} /cancel {job_id}"
        )
        await update.effective_message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def _cmd_retry(self, update: Update, context: CallbackContext) -> None:
        """Retry a failed or cancelled job by re-publishing its payload."""
        if not _is_whitelisted(
            update.effective_user and update.effective_user.id, self._config
        ):
            return
        if not (self._dedup and self._jobs):
            return
        args = context.args if hasattr(context, "args") else []
        if not args:
            await update.effective_message.reply_text("Usage: /retry <job_id>")
            return
        job_id = args[0]
        row = self._dedup.get_job(job_id)
        if not row:
            await update.effective_message.reply_text("Job not found.")
            return
        (job_id, user_id, chat_id, message_id, state, priority, created_at, updated_at, last_error, payload_json) = row
        if state not in ("FAILED", "CANCELLED"):
            await update.effective_message.reply_text(f"Job {job_id} is {state}, cannot retry.")
            return
        try:
            import json as _json
            payload = _json.loads(payload_json) if payload_json else None
            if not payload:
                await update.effective_message.reply_text("No payload stored; cannot retry.")
                return
            await self._jobs.publish_job(payload)
            now_iso = __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime())
            self._dedup.update_job_state(job_id, "PENDING", None, now_iso)
            await update.effective_message.reply_text(f"Re-queued job {job_id}.")
        except Exception as e:
            await update.effective_message.reply_text(f"Retry failed: {e}")

    async def _cmd_cancel(self, update: Update, context: CallbackContext) -> None:
        """Mark a job as cancelled.

        Note: This does not remove an already-queued AMQP message; cancellation
        is advisory and respected by the worker.
        """
        if not _is_whitelisted(
            update.effective_user and update.effective_user.id, self._config
        ):
            return
        if not self._dedup:
            return
        args = context.args if hasattr(context, "args") else []
        if not args:
            await update.effective_message.reply_text("Usage: /cancel <job_id>")
            return
        job_id = args[0]
        now_iso = __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime())
        self._dedup.update_job_state(job_id, "CANCELLED", None, now_iso)
        await update.effective_message.reply_text(f"Cancelled job {job_id}.")

    async def _on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        # Enforce DM-only and whitelist; ignore silently otherwise
        logger.info("Message received", extra={
            "chat_type": update.effective_chat.type if update.effective_chat else "None",
            "user_id": update.effective_user.id if update.effective_user else "None",
            "message_id": (
                update.effective_message.message_id 
                if update.effective_message else "None"
            ),
            "has_media": bool(
                update.effective_message and 
                update.effective_message.media_group_id
            )
        })
        
        if not (update.effective_chat and update.effective_chat.type == "private"):
            logger.info("Ignoring non-DM message")
            return
        if not _is_whitelisted(
            update.effective_user and update.effective_user.id, self._config
        ):
            logger.info(
                "Ignoring non-whitelisted user", 
                extra={
                    "user_id": update.effective_user.id if update.effective_user else "None"
                }
            )
            return
        if not (self._s3 and self._dedup and self._app):
            logger.warning("Services not initialized")
            return
        message = update.effective_message
        
        # Only process messages with actual media content
        if not self._has_media_content(message):
            logger.info("Ignoring message without media content")
            return
            
        logger.info("Processing media message", extra={
            "message_id": message.message_id,
            "media_group_id": message.media_group_id,
            "has_photo": bool(message.photo),
            "has_document": bool(message.document),
            "has_video": bool(message.video)
        })
            
        # Show typing indicator IMMEDIATELY after basic validation - before any workflow tasks
        async with self._typing_context(message.chat_id):
            # Now handle album aggregation and processing within typing context
            items = await self._albums.add_and_maybe_wait(message)
            if items is None:
                logger.info("Message added to album aggregation, waiting for more items")
                return
            else:
                logger.info("Album ready for processing", extra={
                    "item_count": len(items),
                    "message_ids": [m.message_id for m in items]
                })
            
            try:
                # Quota pause at 100%
                if self._quota and self._config.bucket_quota_bytes:
                    ratio = self._quota.used_ratio()
                    if ratio is not None and ratio >= 1.0:
                        text = TelemetryFormatter.format_quota_pause()
                        await message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
                        return

                logger.info(f"Starting batch processing for {len(items)} items")
                
                # Route over-bot-limit items to MTProto via queue before processing others
                to_process = []
                queued_jobs = []
                bot_limit = self._config.bot_api_max_file_size_bytes or (50 * 1024 * 1024)

                async def _extract_file_info(m):
                    """Extract minimal file info for routing and job creation.
                    Variables:
                    - m: telegram.Message - input message
                    Returns dict with keys: file_id:str, file_unique_id:str, file_size:int|None,
                    file_type:str, file_name:str|None, mime_type:str|None
                    """
                    if m.photo:
                        ph = max(m.photo or [], key=lambda p: (p.width or 0) * (p.height or 0))
                        return {
                            "file_id": ph.file_id,
                            "file_unique_id": ph.file_unique_id,
                            "file_size": ph.file_size,
                            "file_type": "photo",
                            "file_name": None,
                            "mime_type": "image/jpeg",
                        }
                    if m.document:
                        return {
                            "file_id": m.document.file_id,
                            "file_unique_id": m.document.file_unique_id,
                            "file_size": m.document.file_size,
                            "file_type": "document",
                            "file_name": m.document.file_name,
                            "mime_type": m.document.mime_type,
                        }
                    if m.video:
                        return {
                            "file_id": m.video.file_id,
                            "file_unique_id": m.video.file_unique_id,
                            "file_size": m.video.file_size,
                            "file_type": "video",
                            "file_name": m.video.file_name,
                            "mime_type": m.video.mime_type,
                        }
                    if m.audio:
                        return {
                            "file_id": m.audio.file_id,
                            "file_unique_id": m.audio.file_unique_id,
                            "file_size": m.audio.file_size,
                            "file_type": "audio",
                            "file_name": m.audio.file_name,
                            "mime_type": m.audio.mime_type,
                        }
                    if m.voice:
                        return {
                            "file_id": m.voice.file_id,
                            "file_unique_id": m.voice.file_unique_id,
                            "file_size": m.voice.file_size,
                            "file_type": "voice",
                            "file_name": None,
                            "mime_type": m.voice.mime_type,
                        }
                    if m.animation:
                        return {
                            "file_id": m.animation.file_id,
                            "file_unique_id": m.animation.file_unique_id,
                            "file_size": m.animation.file_size,
                            "file_type": "animation",
                            "file_name": m.animation.file_name,
                            "mime_type": m.animation.mime_type,
                        }
                    if m.sticker:
                        return {
                            "file_id": m.sticker.file_id,
                            "file_unique_id": m.sticker.file_unique_id,
                            "file_size": m.sticker.file_size,
                            "file_type": "sticker",
                            "file_name": None,
                            "mime_type": None,
                        }
                    if m.video_note:
                        return {
                            "file_id": m.video_note.file_id,
                            "file_unique_id": m.video_note.file_unique_id,
                            "file_size": m.video_note.file_size,
                            "file_type": "video_note",
                            "file_name": None,
                            "mime_type": None,
                        }
                    return None
                    
                async def _check_file_size(m, finfo):
                    """Check if file is too big for Bot API by attempting to get file info.
                    Returns (is_too_big: bool, actual_size: int|None)
                    """
                    try:
                        # Try to get file info from Telegram API
                        tfile = await self._app.bot.get_file(finfo["file_id"])
                        # If we get here, file is accessible via Bot API
                        return False, tfile.file_size
                    except Exception as e:
                        if "File is too big" in str(e):
                            # File is too big for Bot API
                            return True, None
                        else:
                            # Other error, assume file is accessible
                            return False, finfo.get("file_size")

                import datetime as _dt
                import json as _json
                assert self._jobs and self._dedup

                for m in items:
                    finfo = await _extract_file_info(m)
                    logger.info(f"Extracted file info: {finfo}")
                    if not finfo or finfo.get("file_id") is None:
                        to_process.append(m)
                        continue
                    
                    # Check if file is too big for Bot API
                    is_too_big, actual_size = await _check_file_size(m, finfo)
                    size_hint = finfo.get("file_size") or actual_size or 0
                    
                    # Debug logging for file size routing
                    routing_to_mtproto = is_too_big or (size_hint and size_hint > bot_limit)
                    logger.info(
                        f"File size check: size_hint={size_hint}, actual_size={actual_size}, "
                        f"is_too_big={is_too_big}, bot_limit={bot_limit}, "
                        f"routing_to_mtproto={routing_to_mtproto}"
                    )
                    
                    if is_too_big or (size_hint and size_hint > bot_limit):
                        # Create and publish a job
                        job_id = self._jobs.new_job_id()
                        created_at = _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
                        payload = {
                            "job_id": job_id,
                            "user_id": update.effective_user.id if update.effective_user else 0,
                            "chat_id": m.chat.id,
                            "message_id": m.id,
                            "file_info": {
                                "file_id": finfo["file_id"],
                                "file_unique_id": finfo["file_unique_id"],
                                "file_size": size_hint,
                                "file_type": finfo["file_type"],
                                "file_name": finfo.get("file_name"),
                                "mime_type": finfo.get("mime_type"),
                            },
                            "telegram_context": {
                                "forward_origin": m.forward_origin.to_dict() if m.forward_origin else None,  # type: ignore[attr-defined]
                                "caption": m.caption or None,
                                "entities": [e.to_dict() for e in (m.entities or [])],
                                "media_group_id": m.media_group_id,
                            },
                            "job_metadata": {
                                "created_at": created_at,
                                "priority": "normal",
                                "retry_count": 0,
                                "max_retries": 3,
                            },
                        }
                        await self._jobs.publish_job(payload)
                        self._dedup.upsert_job(job_id, payload["user_id"], payload["chat_id"], payload["message_id"], "PENDING", 4, created_at, _json.dumps(payload))
                        queued_jobs.append(job_id)
                    else:
                        to_process.append(m)

                # Acknowledge queued jobs to the user (emoji-rich with one-click commands)
                suppress_response = False
                if queued_jobs:
                    suppress_response = True
                    await message.reply_text(
                        TelemetryFormatter.format_jobs_queued(queued_jobs),
                        parse_mode=ParseMode.MARKDOWN,
                    )

                # Process remaining items via Bot API
                if to_process:
                    res = await process_batch(
                        self._config, self._s3, self._dedup, self._app.bot, to_process
                    )
                else:
                    # Nothing to process via Bot API
                    class _Dummy:
                        outcomes = []
                        base_path = ""
                        total_bytes_uploaded = 0
                    res = _Dummy()
                
                logger.info(f"Batch processing completed successfully")
                
                # Check if the entire batch failed (all items skipped or failed)
                successful_items = [
                    o for o in res.outcomes if o.s3_key and not o.skipped_reason
                ]
                failed_items = [o for o in res.outcomes if o.skipped_reason]
                
                if not queued_jobs and not successful_items and failed_items:
                    # All items failed - send failure message instead of success
                    # Extract factual failure reasons from the outcomes
                    failure_reasons = []
                    for outcome in failed_items:
                        if outcome.skipped_reason:
                            if outcome.skipped_reason == "exceeds_bot_limit":
                                size_str = (
                                    f"{outcome.size_bytes or 'unknown'} bytes" 
                                    if outcome.size_bytes else "unknown size"
                                )
                                failure_reasons.append(
                                    f"File {outcome.ordinal}: exceeds 50MB limit ({size_str})"
                                )
                            elif outcome.skipped_reason == "exceeds_cfg_limit":
                                size_str = (
                                    f"{outcome.size_bytes or 'unknown'} bytes" 
                                    if outcome.size_bytes else "unknown size"
                                )
                                failure_reasons.append(
                                    f"File {outcome.ordinal}: exceeds configured limit ({size_str})"
                                )
                            elif outcome.skipped_reason == "download_failed":
                                failure_reasons.append(
                                    f"File {outcome.ordinal}: download failed"
                                )
                            elif outcome.skipped_reason == "album_validation_failed":
                                failure_reasons.append(
                                    f"File {outcome.ordinal}: validation failed"
                                )
                            elif outcome.skipped_reason == "no_media":
                                failure_reasons.append(
                                    f"File {outcome.ordinal}: no media content"
                                )
                            else:
                                failure_reasons.append(
                                    f"File {outcome.ordinal}: {outcome.skipped_reason}"
                                )
                    
                    # Combine all failure reasons
                    if len(failure_reasons) > 1:
                        reason = "Album failures:\n• " + "\n• ".join(failure_reasons)
                    else:
                        reason = failure_reasons[0] if failure_reasons else "Unknown failure"
                    
                    text = TelemetryFormatter.format_ingestion_failed(
                        reason=reason,
                        item_count=len(items)
                    )
                    await message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
                elif not queued_jobs:
                    # Some or all items succeeded - send success telemetry
                    dedup_ordinals = [
                        o.ordinal for o in res.outcomes if o.is_duplicate
                    ]
                    media_types = list({o.type for o in res.outcomes if o.s3_key})
                    skipped = [o for o in res.outcomes if o.skipped_reason]
                    
                    telemetry_data = TelemetryData(
                        files_count=len(successful_items),
                        media_types=media_types,
                        base_path=res.base_path,
                        dedup_count=len(dedup_ordinals),
                        total_bytes=res.total_bytes_uploaded,
                        skipped_count=len(skipped)
                    )
                    
                    ack = TelemetryFormatter.format_ingestion_ack(telemetry_data)
                    await message.reply_text(ack, parse_mode=ParseMode.MARKDOWN)
            except Exception as e:
                logger.exception(
                    f"ingestion failed for message {message.message_id}: {str(e)}"
                )
                # Extract factual error details
                error_str = str(e)
                if "File is too big" in error_str:
                    reason = "Telegram API error: File is too big (exceeds 50MB limit)"
                elif "album_validation_failed" in error_str:
                    reason = "Album validation failed: " + error_str
                elif "download_failed" in error_str:
                    reason = "Download error: " + error_str
                else:
                    reason = f"Processing error: {error_str}"
                
                text = TelemetryFormatter.format_ingestion_failed(
                    reason=reason, 
                    item_count=len(items)
                )
                await message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


    async def _cmd_purge(self, update: Update, context: CallbackContext) -> None:
        """Purge entire system: storage bucket, database, and job queue.
        
        This is a DESTRUCTIVE operation that requires confirmation.
        Use only for debugging or security purposes.
        """
        if not _is_whitelisted(
            update.effective_user and update.effective_user.id, self._config
        ):
            return
        
        # Check if this is a confirmation
        args = context.args if hasattr(context, "args") else []
        
        if not args or args[0] != "confirm":
            # Show warning and require confirmation
            warning_text = (
                f"⚠️ **SYSTEM PURGE WARNING** ⚠️\n\n"
                f"This command will **PERMANENTLY DELETE**:\n"
                f"• All files in storage bucket\n"
                f"• All database records (files, jobs, auth)\n"
                f"• All pending jobs in queue\n\n"
                f"**This action cannot be undone!**\n\n"
                f"To proceed, use:\n"
                f"`/purge confirm`\n\n"
                f"⚠️ **Use only for debugging or security!**"
            )
            await update.effective_message.reply_text(
                warning_text, parse_mode=ParseMode.MARKDOWN
            )
            return
        
        # User confirmed - proceed with purge
        try:
            await update.effective_message.reply_text(
                "🔄 **Starting system purge...**\n\n"
                "This may take a few moments. Please wait."
            )
            
            purge_results = {}
            
            # 1. Purge storage bucket
            if self._s3:
                try:
                    deleted_files = self._s3.purge_bucket()
                    purge_results["storage"] = deleted_files
                except Exception as e:
                    purge_results["storage_error"] = str(e)
            
            # 2. Purge database
            if self._dedup:
                try:
                    db_counts = self._dedup.purge_all()
                    purge_results["database"] = db_counts
                except Exception as e:
                    purge_results["database_error"] = str(e)
            
            # 3. Purge job queue
            if self._jobs:
                try:
                    purged_jobs = await self._jobs.purge_queue()
                    purge_results["queue"] = purged_jobs
                except Exception as e:
                    purge_results["queue_error"] = str(e)
            
            # Format results
            result_text = "✅ **System Purge Complete**\n\n"
            
            if "storage" in purge_results:
                result_text += f"🗂️ **Storage:** {purge_results['storage']} files deleted\n"
            if "storage_error" in purge_results:
                result_text += f"❌ **Storage Error:** {purge_results['storage_error']}\n"
            
            if "database" in purge_results:
                db_info = purge_results["database"]
                result_text += f"🗄️ **Database:** {db_info.get('files', 0)} files, "
                result_text += f"{db_info.get('jobs', 0)} jobs, "
                result_text += f"{db_info.get('auth_secrets', 0)} secrets deleted\n"
            if "database_error" in purge_results:
                result_text += f"❌ **Database Error:** {purge_results['database_error']}\n"
            
            if "queue" in purge_results:
                result_text += f"📥 **Queue:** {purge_results['queue']} jobs purged\n"
            if "queue_error" in purge_results:
                result_text += f"❌ **Queue Error:** {purge_results['queue_error']}\n"
            
            result_text += "\n🚀 **System has been reset to clean state**"
            
            await update.effective_message.reply_text(
                result_text, parse_mode=ParseMode.MARKDOWN
            )
            
        except Exception as e:
            error_text = f"❌ **Purge failed:** {str(e)}\n\nContact administrator."
            await update.effective_message.reply_text(
                error_text, parse_mode=ParseMode.MARKDOWN
            )

