"""Async RabbitMQ job manager for MTProto large-file processing.

This module encapsulates:
- Durable exchange/queue declaration with dead-letter exchange (DLX)
- JSON job serialization with minimal schema validation
- Publishing jobs with persistence and priority

Types/structures:
- Class `JobManager`: lifecycle and publish API
  - `config: AppConfig` (dependency)
  - `connection`: aio-pika RobustConnection
  - `channel`: aio-pika RobustChannel
  - `exchange`: aio-pika Exchange (direct)

Job message schema (dict[str, Any]):
- job_id: str (UUIDv4)
- user_id: int
- chat_id: int
- message_id: int
- file_info: dict with file_id: str, file_unique_id: str, file_size: int|None,
             file_type: str, file_name: str|None, mime_type: str|None
- telegram_context: dict with forward_origin: dict|None, caption: str|None,
                    entities: list|None, media_group_id: str|None
- job_metadata: dict with created_at: str(ISO8601 UTC), priority: str,
                retry_count: int, max_retries: int

All functions have detailed comments including variable names and data types.
"""

from __future__ import annotations
import json
import logging
import uuid
from typing import Any, Dict, Optional
from urllib.parse import quote

import aio_pika

from ..runtime.config import AppConfig


logger = logging.getLogger("teltubby.queue")


class JobManager:
    """Manage connection to RabbitMQ and publish jobs to durable queues.

    Attributes:
    - _config: AppConfig - application configuration with RabbitMQ settings
    - _connection: aio_pika.RobustConnection | None - resilient AMQP connection
    - _channel: aio_pika.RobustChannel | None - channel used for topology and publish
    - _exchange: aio_pika.Exchange | None - direct exchange for job routing
    - _dlx: aio_pika.Exchange | None - dead-letter exchange for failed jobs
    """

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._connection: Optional[aio_pika.RobustConnection] = None
        self._channel: Optional[aio_pika.RobustChannel] = None
        self._exchange: Optional[aio_pika.Exchange] = None
        self._dlx: Optional[aio_pika.Exchange] = None

    async def initialize(self) -> None:
        """Open connection and declare exchanges/queues as durable.

        Creates a direct exchange for jobs and a DLX for failed jobs.
        Declares the main queue bound to the job exchange and a dead-letter queue
        bound to the DLX. Queue arguments configure DLX and persistence.
        """
        vhost_quoted = quote(self._config.rabbitmq_vhost or "/", safe="")
        url = (
            f"amqp://{self._config.rabbitmq_username}:"
            f"{self._config.rabbitmq_password}@{self._config.rabbitmq_host}:"
            f"{self._config.rabbitmq_port}/{vhost_quoted}"
        )
        self._connection = await aio_pika.connect_robust(url)
        self._channel = await self._connection.channel()

        # Exchanges
        self._dlx = await self._channel.declare_exchange(
            self._config.job_dlx_exchange,
            type=aio_pika.ExchangeType.DIRECT,
            durable=True,
        )
        self._exchange = await self._channel.declare_exchange(
            self._config.job_exchange,
            type=aio_pika.ExchangeType.DIRECT,
            durable=True,
        )

        # Dead-letter queue
        dlq = await self._channel.declare_queue(
            self._config.job_dead_letter_queue, durable=True
        )
        await dlq.bind(self._dlx, routing_key=self._config.job_dead_letter_queue)

        # Main queue with DLX arguments
        args: Dict[str, Any] = {
            "x-dead-letter-exchange": self._config.job_dlx_exchange,
            "x-dead-letter-routing-key": self._config.job_dead_letter_queue,
            # Optional: support for per-message priority (0-9)
            "x-max-priority": 9,
        }
        q = await self._channel.declare_queue(
            self._config.job_queue_name,
            durable=True,
            arguments=args,
        )
        await q.bind(self._exchange, routing_key=self._config.job_queue_name)

        logger.info(
            "RabbitMQ topology declared",
            extra={
                "exchange": self._config.job_exchange,
                "queue": self._config.job_queue_name,
                "dlx": self._config.job_dlx_exchange,
                "dlq": self._config.job_dead_letter_queue,
            },
        )

    async def close(self) -> None:
        """Close AMQP channel and connection safely."""
        try:
            if self._channel:
                await self._channel.close()
        finally:
            self._channel = None
            if self._connection:
                await self._connection.close()
            self._connection = None

    @staticmethod
    def _validate_job_payload(payload: Dict[str, Any]) -> None:
        """Validate minimal required fields in `payload`.

        Parameters:
        - payload: dict[str, Any] - job message to validate

        Raises:
        - ValueError: when required fields are missing or types are invalid
        """
        required_top = [
            "job_id",
            "user_id",
            "chat_id",
            "message_id",
            "file_info",
            "telegram_context",
            "job_metadata",
        ]
        for key in required_top:
            if key not in payload:
                raise ValueError(f"missing field: {key}")

        fi = payload.get("file_info") or {}
        for k in ["file_id", "file_unique_id", "file_type"]:
            if k not in fi:
                raise ValueError(f"missing file_info.{k}")

        jm = payload.get("job_metadata") or {}
        for k in ["created_at", "priority", "retry_count", "max_retries"]:
            if k not in jm:
                raise ValueError(f"missing job_metadata.{k}")

    async def publish_job(self, payload: Dict[str, Any], priority: int = 4) -> None:
        """Publish a validated job message to the main queue.

        Parameters:
        - payload: dict[str, Any] - validated job payload to serialize and publish
        - priority: int - message priority (0..9), default 4
        """
        if not self._channel or not self._exchange:
            raise RuntimeError("JobManager is not initialized")

        # Validate schema before publish
        self._validate_job_payload(payload)

        # Serialize to compact JSON bytes
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        msg = aio_pika.Message(
            body=body,
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            priority=max(0, min(priority, 9)),
            content_type="application/json",
            type="teltubby.large_file.job",
            headers={"schema": "1.0"},
        )
        await self._exchange.publish(
            msg, routing_key=self._config.job_queue_name
        )
        logger.info(
            "Published job",
            extra={"job_id": payload.get("job_id"), "priority": priority},
        )

    @staticmethod
    def new_job_id() -> str:
        """Generate a new UUIDv4 job id as a string."""
        return str(uuid.uuid4())

    async def get_queue_depth(self) -> int:
        """Return the approximate number of ready messages in the main queue."""
        if not self._channel:
            raise RuntimeError("JobManager is not initialized")
        # passive declare to fetch message_count
        q = await self._channel.declare_queue(
            self._config.job_queue_name, durable=True, passive=True
        )
        # declaration_result may be None in some aio-pika versions; guard it
        try:
            return int(q.declaration_result.message_count)  # type: ignore[attr-defined]
        except Exception:
            return 0

    async def purge_queue(self) -> int:
        """Purge all messages from the main job queue and return count of purged messages.
        
        This is a destructive operation that removes ALL pending jobs from the queue.
        Use with extreme caution and only for debugging/security purposes.
        
        Returns:
        - int: Number of messages purged
        """
        if not self._channel:
            raise RuntimeError("JobManager is not initialized")
        
        # Purge the main queue
        q = await self._channel.declare_queue(
            self._config.job_queue_name, durable=True, passive=True
        )
        
        # Get message count before purging
        try:
            message_count = int(q.declaration_result.message_count)  # type: ignore[attr-defined]
        except Exception:
            message_count = 0
        
        # Purge all messages
        await q.purge()
        
        # Also purge the dead letter queue
        dlq = await self._channel.declare_queue(
            self._config.job_dead_letter_queue, durable=True, passive=True
        )
        
        try:
            dlq_count = int(dlq.declaration_result.message_count)  # type: ignore[attr-defined]
        except Exception:
            dlq_count = 0
        
        await dlq.purge()
        
        return message_count + dlq_count


