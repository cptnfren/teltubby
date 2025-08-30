"""Telegram bot service for teltubby.

Provides whitelist enforcement, DM-only handling, mode selection (polling/webhook),
basic command handlers (/start, /help, /status, /quota, /mode, /db_maint), and a
placeholder ingestion pipeline to be expanded in subsequent edits.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

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

from ..metrics.registry import (
    DEDUP_HITS,
    INGESTED_BYTES,
    INGESTED_MESSAGES,
    MINIO_BUCKET_USED_RATIO,
    PROCESSING_SECONDS,
    SKIPPED_ITEMS,
)
from ..runtime.config import AppConfig
from ..storage.s3_client import S3Client
from ..db.dedup import DedupIndex
from ..ingest.album_aggregator import AlbumAggregator
from ..ingest.pipeline import process_batch
from ..quota.quota import QuotaManager


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

    async def start(self) -> None:
        builder = (
            ApplicationBuilder()
            .token(self._config.telegram_bot_token)
            .rate_limiter(AIORateLimiter())
        )
        self._app = builder.build()

        # Commands
        self._app.add_handler(CommandHandler(["start", "help"], self._cmd_start))
        self._app.add_handler(CommandHandler("status", self._cmd_status))
        self._app.add_handler(CommandHandler("quota", self._cmd_quota))
        self._app.add_handler(CommandHandler("mode", self._cmd_mode))
        self._app.add_handler(CommandHandler("db_maint", self._cmd_db_maint))

        # Ingestion: any message with media in DMs
        self._app.add_handler(
            MessageHandler(filters.ALL, self._on_message),
        )

        # Initialize support services
        self._s3 = S3Client(self._config)
        self._dedup = DedupIndex(self._config)
        self._quota = QuotaManager(self._config, self._s3)

        # Lifecycle per PTB v21
        await self._app.initialize()
        await self._app.start()
        if self._config.telegram_mode == "webhook":
            await self._app.updater.start_webhook(listen="0.0.0.0", port=8080)
            if self._config.webhook_url:
                await self._app.bot.set_webhook(url=self._config.webhook_url)
        else:
            await self._app.updater.start_polling(drop_pending_updates=True)

        logger.info("bot started", extra={"mode": self._config.telegram_mode})

    async def stop(self) -> None:
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

    async def _cmd_start(self, update: Update, context: CallbackContext) -> None:
        if not _is_whitelisted(update.effective_user and update.effective_user.id, self._config):
            return
        text = (
            "Send me forwarded or copied messages in DM. I'll archive media to MinIO "
            "with deterministic filenames and JSON, enforcing deduplication."
        )
        await update.effective_message.reply_text(text)

    async def _cmd_status(self, update: Update, context: CallbackContext) -> None:
        if not _is_whitelisted(update.effective_user and update.effective_user.id, self._config):
            return
        used_ratio = self._quota.used_ratio() if self._quota else None
        pct = f"{used_ratio*100:.1f}%" if used_ratio is not None else "unknown"
        text = f"Mode: {self._config.telegram_mode}. MinIO used: {pct}."
        await update.effective_message.reply_text(text)

    async def _cmd_quota(self, update: Update, context: CallbackContext) -> None:
        if not _is_whitelisted(update.effective_user and update.effective_user.id, self._config):
            return
        if not self._quota:
            return
        used_ratio = self._quota.used_ratio()
        if used_ratio is None:
            await update.effective_message.reply_text("Quota unknown (no bucket quota configured).")
            return
        await update.effective_message.reply_text(f"Bucket used: {used_ratio*100:.1f}%")

    async def _cmd_mode(self, update: Update, context: CallbackContext) -> None:
        if not _is_whitelisted(update.effective_user and update.effective_user.id, self._config):
            return
        await update.effective_message.reply_text(f"Mode: {self._config.telegram_mode}")

    async def _cmd_db_maint(self, update: Update, context: CallbackContext) -> None:
        if not _is_whitelisted(update.effective_user and update.effective_user.id, self._config):
            return
        if self._dedup:
            self._dedup.vacuum()
        await update.effective_message.reply_text("DB VACUUM completed.")

    async def _on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        # Enforce DM-only and whitelist; ignore silently otherwise
        if not (update.effective_chat and update.effective_chat.type == "private"):
            return
        if not _is_whitelisted(update.effective_user and update.effective_user.id, self._config):
            return
        if not (self._s3 and self._dedup and self._app):
            return
        message = update.effective_message
        items = await self._albums.add_and_maybe_wait(message)
        if items is None:
            return
        # Process batch
        try:
            # Quota pause at 100%
            if self._quota and self._config.bucket_quota_bytes:
                ratio = self._quota.used_ratio()
                if ratio is not None and ratio >= 1.0:
                    await message.reply_text("Ingestion paused: bucket at 100% capacity.")
                    return

            res = await process_batch(self._config, self._s3, self._dedup, self._app.bot, items)
            # Build minimal ack (will enrich later)
            dedup_ordinals = [o.ordinal for o in res.outcomes if o.is_duplicate]
            media_types = list({o.type for o in res.outcomes if o.s3_key})
            skipped = [o for o in res.outcomes if o.skipped_reason]
            ack = (
                f"files={len([o for o in res.outcomes if o.s3_key])} "
                f"types={','.join(media_types)} base={res.base_path} "
                f"dedup={len(dedup_ordinals)} bytes={res.total_bytes_uploaded} "
                f"skipped={len(skipped)}"
            )
            await message.reply_text(ack)
        except Exception as e:
            logger.exception("ingestion failed")
            await message.reply_text("Ingestion failed. Please try again.")

