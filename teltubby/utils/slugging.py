"""Slugging and filename construction utilities.

Implements transliteration (Cyrillic→Latin), safe charset enforcement, caption
snippet extraction, and deterministic filename generation as per requirements.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
from dataclasses import dataclass
from typing import Optional

from slugify import slugify
from unidecode import unidecode


SAFE_MAX_FILENAME = 120


def to_safe_slug(text: str) -> str:
    translit = unidecode(text or "")
    return slugify(translit, lowercase=True, regex_pattern=r"[^a-zA-Z0-9._-]+")


def caption_snippet(caption: Optional[str], num_words: int = 6) -> str:
    if not caption:
        return ""
    words = re.findall(r"[\w'-]+", unidecode(caption))
    if not words:
        return ""
    snippet = "-".join(words[: num_words])
    return to_safe_slug(snippet)


def build_filename(
    message_ts_utc: _dt.datetime,
    chat_or_source: str,
    sender: str,
    message_id: int,
    media_group_id: Optional[str],
    ordinal: int,
    caption: Optional[str],
    ext: str,
) -> str:
    ts = message_ts_utc.strftime("%Y%m%d-%H%M%S")
    chat_part = to_safe_slug(chat_or_source)
    sender_part = to_safe_slug(sender) if sender else "unknown"
    group_part = f"-g{media_group_id}" if media_group_id else ""
    cap_part = caption_snippet(caption)
    base = f"{ts}_{chat_part}_{sender_part}_m{message_id}{group_part}_{ordinal:03d}"
    if cap_part:
        base = f"{base}_{cap_part}"
    name = f"{base}.{ext}"
    if len(name) > SAFE_MAX_FILENAME:
        overflow = len(name) - SAFE_MAX_FILENAME
        base = base[:-overflow]
        name = f"{base}.{ext}"
    return name

