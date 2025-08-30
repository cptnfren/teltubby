"""Ingestion pipeline: download, dedup, slug, and upload to S3, then JSON.

Implements the core steps for a batch (single message or album group).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import io
import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from telegram import Bot, File, Message, PhotoSize

from ..db.dedup import DedupIndex, DuplicateResult
from ..metrics.registry import (
    DEDUP_HITS,
    INGESTED_BYTES,
    INGESTED_MESSAGES,
    PROCESSING_SECONDS,
    SKIPPED_ITEMS,
)
from ..runtime.config import AppConfig
from ..storage.s3_client import S3Client
from ..utils.slugging import build_filename, to_safe_slug


logger = logging.getLogger("teltubby.ingest")


@dataclass
class ItemOutcome:
    ordinal: int
    type: str
    mime_type: Optional[str]
    size_bytes: Optional[int]
    width: Optional[int]
    height: Optional[int]
    duration: Optional[float]
    file_id: str
    file_unique_id: str
    original_filename: Optional[str]
    sha256: Optional[str]
    s3_key: Optional[str]
    is_duplicate: bool = False
    skipped_reason: Optional[str] = None


@dataclass
class BatchResult:
    base_path: str
    outcomes: List[ItemOutcome] = field(default_factory=list)
    duplicate_of: Optional[str] = None
    dedup_reason: Optional[str] = None
    total_bytes_uploaded: int = 0
    notes: Optional[str] = None


def _pick_highest_photo(message: Message) -> Optional[PhotoSize]:
    if not message.photo:
        return None
    return sorted(message.photo, key=lambda p: (p.width or 0) * (p.height or 0))[-1]


async def _download_to_temp(file: File, chunk_size: int = 1024 * 1024) -> Tuple[str, int, str]:
    """Download Telegram file to a temp path, return (path, size, sha256)."""
    hasher = hashlib.sha256()
    size = 0
    tmp_fd, tmp_path = tempfile.mkstemp(prefix="teltubby_")
    os.close(tmp_fd)
    with open(tmp_path, "wb") as f:
        # telegram lib doesn't expose streaming iterator; buffer in memory chunk
        buf = await file.download_as_bytearray()  # type: ignore[attr-defined]
        f.write(buf)
        size = len(buf)
        hasher.update(buf)
    return tmp_path, size, hasher.hexdigest()


def _detect_ext_and_mime(message: Message) -> Tuple[str, Optional[str], Optional[str]]:
    """Return (type, ext, mime) for the media present in the message."""
    if message.photo:
        return "photo", "jpg", "image/jpeg"
    if message.document:
        name = message.document.file_name or "file.bin"
        ext = (name.rsplit(".", 1)[-1] if "." in name else "bin").lower()
        return "document", ext, message.document.mime_type
    if message.video:
        name = message.video.file_name or "video.mp4"
        ext = (name.rsplit(".", 1)[-1] if "." in name else "mp4").lower()
        return "video", ext, message.video.mime_type
    if message.audio:
        name = message.audio.file_name or "audio.mp3"
        ext = (name.rsplit(".", 1)[-1] if "." in name else "mp3").lower()
        return "audio", ext, message.audio.mime_type
    if message.voice:
        return "voice", "ogg", message.voice.mime_type
    if message.animation:
        name = message.animation.file_name or "anim.mp4"
        ext = (name.rsplit(".", 1)[-1] if "." in name else "mp4").lower()
        return "animation", ext, message.animation.mime_type
    if message.sticker:
        # Telegram stickers: static .webp or .webm (video)
        ext = "webp" if message.sticker.is_animated is False else "webm"
        return "sticker", ext, None
    if message.video_note:
        return "video_note", "mp4", None
    return "unknown", "bin", None


async def process_batch(
    cfg: AppConfig,
    s3: S3Client,
    dedup: DedupIndex,
    bot: Bot,
    messages: List[Message],
) -> BatchResult:
    """Process a batch (single message or album group)."""
    start = time.perf_counter()
    msg0 = messages[0]
    # Compute base path
    ts = dt.datetime.utcfromtimestamp(msg0.date.timestamp())
    year = ts.strftime("%Y")
    month = ts.strftime("%m")
    # chat slug preferring forward origin
    chat_slug_src = None
    if msg0.forward_origin and getattr(msg0.forward_origin, "from_chat", None):
        fchat = msg0.forward_origin.from_chat  # type: ignore[attr-defined]
        chat_slug_src = fchat.username or getattr(fchat, "title", None) or str(fchat.id)
    if not chat_slug_src:
        chat_slug_src = (msg0.chat and msg0.chat.username) or str(msg0.chat.id)
    chat_slug = to_safe_slug(chat_slug_src)
    base_path = f"teltubby/{year}/{month}/{chat_slug}/{msg0.id}/"

    result = BatchResult(base_path=base_path)

    # Sort messages by date as a fallback ordering
    messages_sorted = sorted(messages, key=lambda m: m.date.timestamp())

    for idx, m in enumerate(messages_sorted, start=1):
        mtype, ext, mime = _detect_ext_and_mime(m)
        # Determine primary file and ids
        file_id = None
        file_unique_id = None
        width = height = None
        duration = None
        original_name = None
        size_hint = None
        if m.photo:
            ph = _pick_highest_photo(m)
            if ph:
                file_id = ph.file_id
                file_unique_id = ph.file_unique_id
                width = ph.width
                height = ph.height
                size_hint = ph.file_size
        elif m.document:
            file_id = m.document.file_id
            file_unique_id = m.document.file_unique_id
            original_name = m.document.file_name
            size_hint = m.document.file_size
        elif m.video:
            file_id = m.video.file_id
            file_unique_id = m.video.file_unique_id
            original_name = m.video.file_name
            width = m.video.width
            height = m.video.height
            duration = m.video.duration
            size_hint = m.video.file_size
        elif m.audio:
            file_id = m.audio.file_id
            file_unique_id = m.audio.file_unique_id
            original_name = m.audio.file_name
            duration = m.audio.duration
            size_hint = m.audio.file_size
        elif m.voice:
            file_id = m.voice.file_id
            file_unique_id = m.voice.file_unique_id
            duration = m.voice.duration
            size_hint = m.voice.file_size
        elif m.animation:
            file_id = m.animation.file_id
            file_unique_id = m.animation.file_unique_id
            original_name = m.animation.file_name
            size_hint = m.animation.file_size
        elif m.sticker:
            file_id = m.sticker.file_id
            file_unique_id = m.sticker.file_unique_id
            size_hint = m.sticker.file_size
        elif m.video_note:
            file_id = m.video_note.file_id
            file_unique_id = m.video_note.file_unique_id
            duration = m.video_note.duration
            size_hint = m.video_note.file_size
        else:
            # No binary media to store
            outcome = ItemOutcome(
                ordinal=idx,
                type=mtype,
                mime_type=mime,
                size_bytes=None,
                width=width,
                height=height,
                duration=duration,
                file_id="",
                file_unique_id="",
                original_filename=None,
                sha256=None,
                s3_key=None,
                is_duplicate=False,
                skipped_reason="no_media",
            )
            result.outcomes.append(outcome)
            SKIPPED_ITEMS.inc()
            continue

        assert file_id and file_unique_id

        # Fast-path dedup by file_unique_id
        dres = dedup.check_by_unique_id(file_unique_id)
        if dres.is_duplicate:
            DEDUP_HITS.inc()
            result.duplicate_of = dres.existing_key
            result.dedup_reason = dres.reason
            outcome = ItemOutcome(
                ordinal=idx,
                type=mtype,
                mime_type=mime,
                size_bytes=size_hint,
                width=width,
                height=height,
                duration=duration,
                file_id=file_id,
                file_unique_id=file_unique_id,
                original_filename=original_name,
                sha256=None,
                s3_key=dres.existing_key,
                is_duplicate=True,
            )
            result.outcomes.append(outcome)
            continue

        # Enforce size limits before download if we have a hint
        max_bytes_cfg = cfg.max_file_gb * 1024 * 1024 * 1024
        bot_limit = cfg.bot_api_max_file_size_bytes or (50 * 1024 * 1024)
        if size_hint and (size_hint > bot_limit or size_hint > max_bytes_cfg):
            reason = "exceeds_bot_limit" if size_hint > bot_limit else "exceeds_cfg_limit"
            outcome = ItemOutcome(
                ordinal=idx,
                type=mtype,
                mime_type=mime,
                size_bytes=size_hint,
                width=width,
                height=height,
                duration=duration,
                file_id=file_id,
                file_unique_id=file_unique_id,
                original_filename=original_name,
                sha256=None,
                s3_key=None,
                is_duplicate=False,
                skipped_reason=reason,
            )
            result.outcomes.append(outcome)
            SKIPPED_ITEMS.inc()
            continue

        # Download to temp to compute sha256
        tfile = await bot.get_file(file_id)
        tmp_path, content_size, sha256 = await _download_to_temp(tfile)

        # Enforce size after download when hint missing
        if content_size > bot_limit or content_size > max_bytes_cfg:
            os.remove(tmp_path)
            reason = "exceeds_bot_limit" if content_size > bot_limit else "exceeds_cfg_limit"
            outcome = ItemOutcome(
                ordinal=idx,
                type=mtype,
                mime_type=mime,
                size_bytes=content_size,
                width=width,
                height=height,
                duration=duration,
                file_id=file_id,
                file_unique_id=file_unique_id,
                original_filename=original_name,
                sha256=sha256,
                s3_key=None,
                is_duplicate=False,
                skipped_reason=reason,
            )
            result.outcomes.append(outcome)
            SKIPPED_ITEMS.inc()
            continue

        # Dedup by sha256
        dsha = dedup.check_by_sha256(sha256)
        if dsha.is_duplicate:
            DEDUP_HITS.inc()
            result.duplicate_of = dsha.existing_key
            result.dedup_reason = dsha.reason
            outcome = ItemOutcome(
                ordinal=idx,
                type=mtype,
                mime_type=mime,
                size_bytes=content_size,
                width=width,
                height=height,
                duration=duration,
                file_id=file_id,
                file_unique_id=file_unique_id,
                original_filename=original_name,
                sha256=sha256,
                s3_key=dsha.existing_key,
                is_duplicate=True,
            )
            result.outcomes.append(outcome)
            os.remove(tmp_path)
            continue

        # Build filename and upload
        caption = (m.caption or None)
        fname = build_filename(
            message_ts_utc=ts,
            chat_or_source=chat_slug,
            sender=(m.from_user and (m.from_user.username or str(m.from_user.id))) or "unknown",
            message_id=msg0.id,
            media_group_id=msg0.media_group_id,
            ordinal=idx,
            caption=caption,
            ext=ext,
        )
        key = f"{base_path}{fname}"
        with open(tmp_path, "rb") as f:
            s3.upload_fileobj(key, f, content_size, content_type=mime)
        os.remove(tmp_path)

        dedup.record(sha256=sha256, s3_key=key, size_bytes=content_size, mime=mime, file_unique_id=file_unique_id)
        INGESTED_BYTES.inc(content_size)

        outcome = ItemOutcome(
            ordinal=idx,
            type=mtype,
            mime_type=mime,
            size_bytes=content_size,
            width=width,
            height=height,
            duration=duration,
            file_id=file_id,
            file_unique_id=file_unique_id,
            original_filename=original_name,
            sha256=sha256,
            s3_key=key,
            is_duplicate=False,
        )
        result.outcomes.append(outcome)

    # Write JSON artifact
    json_key = f"{base_path}message.json"
    artifact = _build_json_artifact(cfg, messages_sorted, result)
    data = json.dumps(artifact, separators=(",", ":")).encode("utf-8")
    s3.upload_fileobj(json_key, io.BytesIO(data), len(data), content_type="application/json")

    result.total_bytes_uploaded = sum(o.size_bytes or 0 for o in result.outcomes if o.s3_key and not result.duplicate_of)

    INGESTED_MESSAGES.inc()
    PROCESSING_SECONDS.observe(time.perf_counter() - start)
    return result


def _build_json_artifact(cfg: AppConfig, messages: List[Message], res: BatchResult) -> dict:
    msg0 = messages[0]
    ts_utc = dt.datetime.utcfromtimestamp(msg0.date.timestamp())
    keys = [o.s3_key for o in res.outcomes if o.s3_key]
    telegram_items = []
    for o in res.outcomes:
        telegram_items.append(
            {
                "ordinal": o.ordinal,
                "type": o.type,
                "mime_type": o.mime_type,
                "size_bytes": o.size_bytes,
                "width": o.width,
                "height": o.height,
                "duration": o.duration,
                "file_id": o.file_id,
                "file_unique_id": o.file_unique_id,
                "original_filename": o.original_filename,
                "sha256": o.sha256,
                "s3_key": o.s3_key,
            }
        )
    artifact = {
        "schema_version": "1.0",
        "archive_timestamp_utc": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "message_timestamp_utc": ts_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bucket": cfg.s3_bucket,
        "base_path": res.base_path,
        "files_count": len([k for k in keys if k]),
        "total_bytes_uploaded": res.total_bytes_uploaded,
        "keys": [k for k in keys if k],
        "duplicate_of": res.duplicate_of,
        "dedup_reason": res.dedup_reason,
        "notes": res.notes,
        "telegram": {
            "message_id": str(msg0.id),
            "media_group_id": str(msg0.media_group_id) if msg0.media_group_id else None,
            "chat_id": str(msg0.chat.id),
            "chat_title": getattr(msg0.chat, "title", None),
            "chat_username": msg0.chat.username,
            "sender_id": str(msg0.from_user.id) if msg0.from_user else "",
            "sender_username": msg0.from_user.username if msg0.from_user else None,
            "forward_origin": msg0.forward_origin.to_dict() if msg0.forward_origin else None,  # type: ignore[attr-defined]
            "caption_plain": msg0.caption or None,
            "caption_entities": [e.to_dict() for e in (msg0.caption_entities or [])],
            "entities": [e.to_dict() for e in (msg0.entities or [])],
            "bot_api_max_file_size_bytes": None,  # filled later if available
            "items": telegram_items,
        },
    }
    return artifact

