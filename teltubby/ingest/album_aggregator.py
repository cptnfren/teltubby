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
        import logging
        logger = logging.getLogger("teltubby.album_aggregator")
        
        mgid = message.media_group_id
        if not mgid:
            logger.debug("No media_group_id, processing as single message")
            return [message]

        lock = self._locks.setdefault(mgid, asyncio.Lock())
        async with lock:
            bucket = self._buckets.get(mgid)
            now = time.monotonic()
            
            logger.debug(f"Processing message {message.message_id} for mgid {mgid}")
            
            # ALWAYS check if existing bucket has expired first
            if bucket and not bucket.done:
                elapsed = now - bucket.started_at_monotonic
                logger.debug(f"Bucket {mgid} elapsed: {elapsed:.1f}s, window: {self._window}s")
                if elapsed >= self._window:
                    logger.info(f"Bucket {mgid} expired, processing {len(bucket.items)} items")
                    bucket.done = True
                    items = bucket.items.copy()  # Copy before cleanup
                    # cleanup
                    del self._buckets[mgid]
                    del self._locks[mgid]
                    return items
            
            # Create new bucket if needed
            if bucket is None:
                logger.debug(f"Creating new bucket for mgid {mgid}")
                bucket = AlbumBucket(started_at_monotonic=now)
                self._buckets[mgid] = bucket
            
            if bucket.done:
                logger.debug(f"Bucket {mgid} already done, treating as single message")
                return [message]
                
            bucket.items.append(message)
            logger.debug(f"Added message {message.message_id} to bucket {mgid}, total items: {len(bucket.items)}")

            # Check if this message triggers the timeout
            elapsed = now - bucket.started_at_monotonic
            if elapsed >= self._window:
                logger.info(f"Bucket {mgid} timeout reached, processing {len(bucket.items)} items")
                bucket.done = True
                items = bucket.items.copy()  # Copy before cleanup
                # cleanup
                del self._buckets[mgid]
                del self._locks[mgid]
                return items
            else:
                logger.debug(f"Bucket {mgid} waiting for more items, remaining: {self._window - elapsed:.1f}s")
                return None

    async def pop_ready_albums(self) -> List[List[Message]]:
        """Finalize and return albums whose window has elapsed.

        This allows a periodic finalizer to flush albums even if no
        further messages arrive after the timeout window.
        """
        import logging
        logger = logging.getLogger("teltubby.album_aggregator")

        ready: List[List[Message]] = []
        now = time.monotonic()

        # Collect candidate media_group_ids first to avoid dict mutation
        candidates: List[str] = []
        for mgid, bucket in self._buckets.items():
            if bucket.done or (now - bucket.started_at_monotonic) >= self._window:
                candidates.append(mgid)

        for mgid in candidates:
            lock = self._locks.setdefault(mgid, asyncio.Lock())
            if lock.locked():
                # Skip if another coroutine is working on this bucket
                continue
            async with lock:
                bucket = self._buckets.get(mgid)
                if not bucket:
                    continue
                elapsed = now - bucket.started_at_monotonic
                if not bucket.done and elapsed < self._window:
                    continue
                items = bucket.items.copy()
                del self._buckets[mgid]
                del self._locks[mgid]
                logger.info(
                    "Finalizing album from periodic finalizer",
                    extra={
                        "media_group_id": mgid,
                        "item_count": len(items),
                    },
                )
                ready.append(items)

        return ready

