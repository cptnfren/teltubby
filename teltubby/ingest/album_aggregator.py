"""In-memory album aggregation with timeout window.

Collects messages sharing the same `media_group_id` for a short window to ensure
we process the full album together. Falls back to whatever arrived when the
window elapses.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from telegram import Message


@dataclass
class AlbumBucket:
    started_at_monotonic: float
    items: List[Message] = field(default_factory=list)
    done: bool = False


class AlbumAggregator:
    def __init__(self, window_seconds: int) -> None:
        self._window = window_seconds
        self._buckets: Dict[str, AlbumBucket] = {}
        self._locks: Dict[str, asyncio.Lock] = {}

    async def add_and_maybe_wait(self, message: Message) -> Optional[List[Message]]:
        """Add a message to its album bucket and return the finalized list if ready.

        Returns:
            - None if waiting for more items within the aggregation window
            - List[Message] when the bucket window has elapsed and the album is ready
        """
        mgid = message.media_group_id
        if not mgid:
            return [message]

        lock = self._locks.setdefault(mgid, asyncio.Lock())
        async with lock:
            bucket = self._buckets.get(mgid)
            now = time.monotonic()
            if bucket is None:
                bucket = AlbumBucket(started_at_monotonic=now)
                self._buckets[mgid] = bucket
            if bucket.done:
                # already finalized; treat as single
                return [message]
            bucket.items.append(message)

            elapsed = now - bucket.started_at_monotonic
            if elapsed >= self._window:
                bucket.done = True
                items = bucket.items
                # cleanup
                del self._buckets[mgid]
                return items
            else:
                return None

