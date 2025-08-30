"""Prometheus metrics registry and metric objects used across the app."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, REGISTRY


INGESTED_MESSAGES = Counter(
    "teltubby_ingested_messages_total", "Total number of messages ingested"
)

INGESTED_BYTES = Counter(
    "teltubby_ingested_bytes_total", "Total bytes uploaded to S3"
)

DEDUP_HITS = Counter(
    "teltubby_dedup_hits_total", "Total number of dedup hits"
)

SKIPPED_ITEMS = Counter(
    "teltubby_skipped_items_total", "Total number of skipped items"
)

MINIO_BUCKET_USED_RATIO = Gauge(
    "teltubby_minio_bucket_used_ratio", "MinIO bucket used ratio (0..1)"
)

PROCESSING_SECONDS = Histogram(
    "teltubby_processing_seconds", "Processing time per message"
)

# Phase 3: Job queue metrics
JOBS_CREATED = Counter(
    "teltubby_jobs_created_total", "Jobs created and enqueued for MTProto"
)
JOBS_COMPLETED = Counter(
    "teltubby_jobs_completed_total", "Jobs completed successfully by worker"
)
JOBS_FAILED = Counter(
    "teltubby_jobs_failed_total", "Jobs failed in worker"
)
JOBS_RETRIED = Counter(
    "teltubby_jobs_retried_total", "Jobs retried by admin or policy"
)
