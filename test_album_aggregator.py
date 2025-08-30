#!/usr/bin/env python3
"""Test script for album aggregator logic."""

import asyncio
import time
from unittest.mock import Mock

# Mock the telegram Message class
class MockMessage:
    def __init__(self, message_id, media_group_id=None):
        self.message_id = message_id
        self.media_group_id = media_group_id

# Copy the AlbumAggregator class without the background task
class AlbumAggregator:
    def __init__(self, window_seconds: int) -> None:
        self._window = window_seconds
        self._buckets = {}
        self._locks = {}

    def _check_expired_buckets(self):
        """Check for expired buckets and return their mgids."""
        now = time.monotonic()
        expired_mgids = []
        
        for mgid, bucket in self._buckets.items():
            if not bucket.done and (now - bucket.started_at_monotonic) >= self._window:
                expired_mgids.append(mgid)
        
        return expired_mgids

    async def add_and_maybe_wait(self, message):
        """Add a message to its album bucket and return the finalized list if ready."""
        # First, check for any expired buckets and process them
        expired_mgids = self._check_expired_buckets()
        for mgid in expired_mgids:
            bucket = self._buckets[mgid]
            bucket.done = True
            print(f"Bucket {mgid} expired, will be processed on next access")
        
        mgid = message.media_group_id
        if not mgid:
            print("No media_group_id, processing as single message")
            return [message]

        # Simple lock simulation
        if mgid not in self._locks:
            self._locks[mgid] = asyncio.Lock()
        
        async with self._locks[mgid]:
            bucket = self._buckets.get(mgid)
            now = time.monotonic()
            
            print(f"Processing message {message.message_id} for mgid {mgid}")
            print(f"Existing bucket: {bucket is not None}, done: {bucket.done if bucket else 'N/A'}")
            
            # Check if existing bucket has expired
            if bucket and not bucket.done:
                elapsed = now - bucket.started_at_monotonic
                print(f"Existing bucket elapsed: {elapsed:.1f}s, window: {self._window}s")
                if elapsed >= self._window:
                    print(f"Bucket {mgid} expired, processing {len(bucket.items)} items")
                    bucket.done = True
                    items = bucket.items
                    # cleanup
                    del self._buckets[mgid]
                    del self._locks[mgid]
                    return items
            
            # Create new bucket if needed
            if bucket is None:
                print(f"Creating new bucket for mgid {mgid}")
                bucket = AlbumBucket(started_at_monotonic=now)
                self._buckets[mgid] = bucket
            
            if bucket.done:
                print(f"Bucket {mgid} already done, treating as single message")
                return [message]
                
            bucket.items.append(message)
            print(f"Added message {message.message_id} to bucket {mgid}, total items: {len(bucket.items)}")

            # Check if this message triggers the timeout
            elapsed = now - bucket.started_at_monotonic
            print(f"Current elapsed: {elapsed:.1f}s, window: {self._window}s")
            if elapsed >= self._window:
                print(f"Bucket {mgid} timeout reached, processing {len(bucket.items)} items")
                bucket.done = True
                items = bucket.items
                # cleanup
                del self._buckets[mgid]
                del self._locks[mgid]
                return items
            else:
                print(f"Bucket {mgid} waiting for more items, remaining: {self._window - elapsed:.1f}s")
                return None

class AlbumBucket:
    def __init__(self, started_at_monotonic: float) -> None:
        self.started_at_monotonic = started_at_monotonic
        self.items = []
        self.done = False

async def test_album_aggregator():
    """Test the album aggregator with a 2-second window."""
    print("=== Testing Album Aggregator ===")
    
    aggregator = AlbumAggregator(window_seconds=2)
    
    # Create test messages
    msg1 = MockMessage(1, "album1")
    msg2 = MockMessage(2, "album1")
    
    print(f"\n--- Adding first message ---")
    result1 = await aggregator.add_and_maybe_wait(msg1)
    print(f"Result 1: {result1}")
    
    print(f"\n--- Adding second message ---")
    result2 = await aggregator.add_and_maybe_wait(msg2)
    print(f"Result 2: {result2}")
    
    print(f"\n--- Waiting 3 seconds for timeout ---")
    await asyncio.sleep(3)
    
    print(f"\n--- Adding third message (should trigger expired bucket) ---")
    msg3 = MockMessage(3, "album1")
    result3 = await aggregator.add_and_maybe_wait(msg3)
    print(f"Result 3: {result3}")
    
    print(f"\n--- Test completed ---")

if __name__ == "__main__":
    asyncio.run(test_album_aggregator())
