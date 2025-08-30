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
from telegram.constants import ParseMode
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
from ..runtime.config import AppConfig
from ..storage.s3_client import S3Client
from ..db.dedup import DedupIndex
from ..ingest.album_aggregator import AlbumAggregator
from ..ingest.pipeline import process_batch
from ..quota.quota import QuotaManager
from ..utils.telemetry_formatter import TelemetryFormatter, TelemetryData


logger = logging.getLogger("teltubby.bot")


def _is_whitelisted(user_id: Optional[int], cfg: AppConfig) -> bool:
    return user_id is not None and user_id in cfg.telegram_whitelist_ids


class TeltubbyBotService:
    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._app: Optional[Application] = None
        self._s3: Optional[S3Client] = None
        self._dedup: Optional[DedupIndex] = None
        self._albums = AlbumAggregator(window_seconds=config.album_aggregation_window_seconds)
        self._quota: Optional[QuotaManager] = None
        self._finalizer_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        builder = (
            ApplicationBuilder()
            .token(self._config.telegram_bot_token)
            .rate_limiter(AIORateLimiter())
        )
        self._app = builder.build()

        # Commands - must be added BEFORE message handlers
        self._app.add_handler(CommandHandler(["start", "help"], self._cmd_start))
        self._app.add_handler(CommandHandler("status", self._cmd_status))
        self._app.add_handler(CommandHandler("quota", self._cmd_quota))
        self._app.add_handler(CommandHandler("mode", self._cmd_mode))
        self._app.add_handler(CommandHandler("db_maint", self._cmd_db_maint))

        # Ingestion: only messages with media content in DMs
        self._app.add_handler(
            MessageHandler(filters.ALL & filters.ChatType.PRIVATE, self._on_message),
        )

        # Initialize support services
        self._s3 = S3Client(self._config)
        self._dedup = DedupIndex(self._config)
        self._quota = QuotaManager(self._config, self._s3)

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
                            action="typing"
                        )
                        await asyncio.sleep(4)  # Telegram typing expires after ~5 seconds
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.warning("Failed to send typing indicator", extra={"error": str(e)})
        
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
                        
                        logger.info(f"Finalizer starting batch processing for {len(items)} items")
                        
                        # Show typing indicator while processing in finalizer
                        async with self._typing_context(last_msg.chat_id):
                            res = await process_batch(self._config, self._s3, self._dedup, self._app.bot, items)
                        
                        logger.info(
                            "Finalizer processed batch successfully",
                            extra={"message_ids": [m.message_id for m in items], "count": len(items)},
                        )

                        # Check if the entire batch failed (all items skipped or failed)
                        successful_items = [o for o in res.outcomes if o.s3_key and not o.skipped_reason]
                        failed_items = [o for o in res.outcomes if o.skipped_reason]
                        
                        if not successful_items and failed_items:
                            # All items failed - send failure message instead of success
                            # Extract factual failure reasons from the outcomes
                            failure_reasons = []
                            for outcome in failed_items:
                                if outcome.skipped_reason:
                                    if outcome.skipped_reason == "exceeds_bot_limit":
                                        size_str = f"{outcome.size_bytes or 'unknown'} bytes" if outcome.size_bytes else "unknown size"
                                        failure_reasons.append(f"File {outcome.ordinal}: exceeds 50MB limit ({size_str})")
                                    elif outcome.skipped_reason == "exceeds_cfg_limit":
                                        size_str = f"{outcome.size_bytes or 'unknown'} bytes" if outcome.size_bytes else "unknown size"
                                        failure_reasons.append(f"File {outcome.ordinal}: exceeds configured limit ({size_str})")
                                    elif outcome.skipped_reason == "download_failed":
                                        failure_reasons.append(f"File {outcome.ordinal}: download failed")
                                    elif outcome.skipped_reason == "album_validation_failed":
                                        failure_reasons.append(f"File {outcome.ordinal}: validation failed")
                                    elif outcome.skipped_reason == "no_media":
                                        failure_reasons.append(f"File {outcome.ordinal}: no media content")
                                    else:
                                        failure_reasons.append(f"File {outcome.ordinal}: {outcome.skipped_reason}")
                            
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
                        else:
                            # Some or all items succeeded - send success telemetry
                            try:
                                dedup_ordinals = [o.ordinal for o in res.outcomes if o.is_duplicate]
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
        if not _is_whitelisted(update.effective_user and update.effective_user.id, self._config):
            return
        text = TelemetryFormatter.format_start()
        await update.effective_message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def _cmd_status(self, update: Update, context: CallbackContext) -> None:
        if not _is_whitelisted(update.effective_user and update.effective_user.id, self._config):
            return
        
        # Show typing indicator while checking status
        await update.effective_chat.send_action("typing")
        
        used_ratio = self._quota.used_ratio() if self._quota else None
        text = TelemetryFormatter.format_status(self._config.telegram_mode, used_ratio)
        await update.effective_message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def _cmd_quota(self, update: Update, context: CallbackContext) -> None:
        if not _is_whitelisted(update.effective_user and update.effective_user.id, self._config):
            return
        if not self._quota:
            return
        
        # Show typing indicator while calculating quota
        await update.effective_chat.send_action("typing")
        
        used_ratio = self._quota.used_ratio()
        if used_ratio is None:
            await update.effective_message.reply_text("Quota unknown (no bucket quota configured).")
            return
        text = TelemetryFormatter.format_quota(used_ratio)
        await update.effective_message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def _cmd_mode(self, update: Update, context: CallbackContext) -> None:
        if not _is_whitelisted(update.effective_user and update.effective_user.id, self._config):
            return
        text = TelemetryFormatter.format_mode(self._config.telegram_mode)
        await update.effective_message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def _cmd_db_maint(self, update: Update, context: CallbackContext) -> None:
        if not _is_whitelisted(update.effective_user and update.effective_user.id, self._config):
            return
        if self._dedup:
            self._dedup.vacuum()
        text = TelemetryFormatter.format_db_maint()
        await update.effective_message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def _on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        # Enforce DM-only and whitelist; ignore silently otherwise
        logger.info("Message received", extra={
            "chat_type": update.effective_chat.type if update.effective_chat else "None",
            "user_id": update.effective_user.id if update.effective_user else "None",
            "message_id": (update.effective_message.message_id 
                          if update.effective_message else "None"),
            "has_media": bool(update.effective_message and 
                             update.effective_message.media_group_id)
        })
        
        if not (update.effective_chat and update.effective_chat.type == "private"):
            logger.info("Ignoring non-DM message")
            return
        if not _is_whitelisted(update.effective_user and update.effective_user.id, self._config):
            logger.info("Ignoring non-whitelisted user", extra={"user_id": update.effective_user.id if update.effective_user else "None"})
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
            
        items = await self._albums.add_and_maybe_wait(message)
        if items is None:
            logger.info("Message added to album aggregation, waiting for more items")
            return
        else:
            logger.info("Album ready for processing", extra={
                "item_count": len(items),
                "message_ids": [m.message_id for m in items]
            })
        
        # Show typing indicator immediately - before any processing
        try:
            async with self._typing_context(message.chat_id):
                # Quota pause at 100%
                if self._quota and self._config.bucket_quota_bytes:
                    ratio = self._quota.used_ratio()
                    if ratio is not None and ratio >= 1.0:
                        text = TelemetryFormatter.format_quota_pause()
                        await message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
                        return

                logger.info(f"Starting batch processing for {len(items)} items")
                
                # Process batch
                res = await process_batch(self._config, self._s3, self._dedup, self._app.bot, items)
                
                logger.info(f"Batch processing completed successfully")
                
                # Check if the entire batch failed (all items skipped or failed)
                successful_items = [o for o in res.outcomes if o.s3_key and not o.skipped_reason]
                failed_items = [o for o in res.outcomes if o.skipped_reason]
                
                if not successful_items and failed_items:
                    # All items failed - send failure message instead of success
                    # Extract factual failure reasons from the outcomes
                    failure_reasons = []
                    for outcome in failed_items:
                        if outcome.skipped_reason:
                            if outcome.skipped_reason == "exceeds_bot_limit":
                                size_str = f"{outcome.size_bytes or 'unknown'} bytes" if outcome.size_bytes else "unknown size"
                                failure_reasons.append(f"File {outcome.ordinal}: exceeds 50MB limit ({size_str})")
                            elif outcome.skipped_reason == "exceeds_cfg_limit":
                                size_str = f"{outcome.size_bytes or 'unknown'} bytes" if outcome.size_bytes else "unknown size"
                                failure_reasons.append(f"File {outcome.ordinal}: exceeds configured limit ({size_str})")
                            elif outcome.skipped_reason == "download_failed":
                                failure_reasons.append(f"File {outcome.ordinal}: download failed")
                            elif outcome.skipped_reason == "album_validation_failed":
                                failure_reasons.append(f"File {outcome.ordinal}: validation failed")
                            elif outcome.skipped_reason == "no_media":
                                failure_reasons.append(f"File {outcome.ordinal}: no media content")
                            else:
                                failure_reasons.append(f"File {outcome.ordinal}: {outcome.skipped_reason}")
                    
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
                else:
                    # Some or all items succeeded - send success telemetry
                    dedup_ordinals = [o.ordinal for o in res.outcomes if o.is_duplicate]
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
            logger.exception(f"ingestion failed for message {message.message_id}: {str(e)}")
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

