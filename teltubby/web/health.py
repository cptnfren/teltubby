"""Health check endpoints for teltubby.

Provides health status, metrics, and system information for monitoring
and operational visibility.
"""

import asyncio
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
import aio_pika

from ..runtime.config import AppConfig
from ..metrics.registry import REGISTRY

logger = logging.getLogger("teltubby.health")

app = FastAPI(title="Teltubby Health API", version="1.0.0")


def get_config() -> AppConfig:
    """Get application configuration."""
    return AppConfig.from_env()


@app.get("/healthz")
async def health_check() -> Dict[str, Any]:
    """Basic health check endpoint."""
    try:
        # Basic system health
        health_status = {
            "status": "healthy",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version": "1.0.0",
            "environment": {
                "python_version": f"{os.sys.version_info.major}.{os.sys.version_info.minor}.{os.sys.version_info.micro}",
                "platform": os.name,
            }
        }
        
        # Check database connectivity
        try:
            config = get_config()
            conn = sqlite3.connect(config.sqlite_path)
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            conn.close()
            health_status["database"] = {"status": "healthy", "path": config.sqlite_path}
        except Exception as e:
            health_status["database"] = {"status": "unhealthy", "error": str(e)}
            health_status["status"] = "degraded"
        
        # Check S3/MinIO connectivity
        try:
            from ..storage.s3_client import S3Client
            s3_client = S3Client(config)
            s3_client.ensure_bucket()
            health_status["storage"] = {"status": "healthy", "endpoint": config.s3_endpoint}
        except Exception as e:
            health_status["storage"] = {"status": "unhealthy", "error": str(e)}
            health_status["status"] = "degraded"
        
        # Check RabbitMQ connectivity
        try:
            vhost_quoted = config.rabbitmq_vhost or "/"
            url = f"amqp://{config.rabbitmq_username}:{config.rabbitmq_password}@{config.rabbitmq_host}:{config.rabbitmq_port}/{vhost_quoted}"
            connection = await aio_pika.connect_robust(url)
            channel = await connection.channel()
            
            # Check queue status
            queue = await channel.declare_queue(config.job_queue_name, passive=True)
            queue_info = await queue.declare()
            
            health_status["rabbitmq"] = {
                "status": "healthy",
                "host": config.rabbitmq_host,
                "port": config.rabbitmq_port,
                "queue": {
                    "name": config.job_queue_name,
                    "messages": queue_info.message_count,
                    "consumers": queue_info.consumer_count
                }
            }
            
            await channel.close()
            await connection.close()
            
        except Exception as e:
            health_status["rabbitmq"] = {"status": "unhealthy", "error": str(e)}
            health_status["status"] = "degraded"
        
        # Check MTProto worker status (optional - Docker may not be available in container)
        try:
            health_status["mtproto_worker"] = await get_mtproto_worker_status()
        except Exception as e:
            health_status["mtproto_worker"] = {"status": "unknown", "error": "Docker commands not available in container"}
        
        return health_status
        
    except Exception as e:
        logger.exception("Health check failed")
        raise HTTPException(status_code=500, detail=f"Health check failed: {str(e)}")


async def get_mtproto_worker_status() -> Dict[str, Any]:
    """Get MTProto worker status from Docker container."""
    try:
        import subprocess
        import json as json_lib
        
        # Get worker container status
        result = subprocess.run(
            ["docker", "ps", "--filter", "name=mtworker", "--format", "{{.Status}}"],
            capture_output=True, text=True, timeout=10
        )
        
        if result.returncode != 0:
            return {"status": "unknown", "error": "Failed to query Docker"}
        
        container_status = result.stdout.strip()
        if not container_status:
            return {"status": "stopped", "container": "not_found"}
        
        # Get recent worker logs for status
        log_result = subprocess.run(
            ["docker", "logs", "mtworker", "--tail", "20"],
            capture_output=True, text=True, timeout=10
        )
        
        logs = log_result.stdout if log_result.returncode == 0 else ""
        
        # Parse logs for key status indicators
        status_indicators = {
            "authenticated": "MTProto client started" in logs,
            "worker_running": "worker started" in logs,
            "session_monitoring": "MTProto session monitoring started" in logs,
            "simulate_mode": "simulate mode enabled" in logs,
            "last_activity": None
        }
        
        # Extract last activity timestamp
        import re
        timestamp_match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', logs)
        if timestamp_match:
            status_indicators["last_activity"] = timestamp_match.group(1)
        
        # Determine overall status
        if "simulate mode enabled" in logs:
            overall_status = "simulate_mode"
        elif "MTProto client started" in logs and "worker started" in logs:
            overall_status = "healthy"
        elif "MTProto client started" in logs:
            overall_status = "authenticating"
        else:
            overall_status = "starting"
        
        return {
            "status": overall_status,
            "container": container_status,
            "indicators": status_indicators,
            "logs_sample": logs.split('\n')[-5:] if logs else []  # Last 5 lines
        }
        
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/metrics")
async def metrics() -> PlainTextResponse:
    """Prometheus metrics endpoint."""
    try:
        from prometheus_client import generate_latest
        metrics_data = generate_latest(REGISTRY)
        return PlainTextResponse(content=metrics_data.decode('utf-8'))
    except Exception as e:
        logger.exception("Metrics generation failed")
        raise HTTPException(status_code=500, detail=f"Metrics generation failed: {str(e)}")


@app.get("/status")
async def detailed_status() -> Dict[str, Any]:
    """Detailed system status including all components."""
    try:
        config = get_config()
        
        status = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "system": {
                "config": {
                    "telegram_mode": config.telegram_mode,
                    "album_aggregation_window": f"{config.album_aggregation_window_seconds}s",
                    "max_file_size": f"{config.max_file_gb}GB",
                    "bot_api_max_file_size": f"{config.bot_api_max_file_size_bytes / (1024*1024):.1f}MB",
                    "concurrency": config.concurrency,
                    "dedup_enabled": config.dedup_enable
                },
                "storage": {
                    "s3_endpoint": config.s3_endpoint,
                    "bucket": config.s3_bucket,
                    "region": config.s3_region,
                    "force_path_style": config.s3_force_path_style
                },
                "queue": {
                    "rabbitmq_host": config.rabbitmq_host,
                    "rabbitmq_port": config.rabbitmq_port,
                    "job_queue": config.job_queue_name,
                    "dead_letter_queue": config.job_dead_letter_queue
                },
                "mtproto": {
                    "api_id_configured": bool(config.mtproto_api_id),
                    "api_hash_configured": bool(config.mtproto_api_hash),
                    "phone_configured": bool(config.mtproto_phone_number),
                    "session_path": config.mtproto_session_path
                }
            }
        }
        
        # Add health check data
        health_data = await health_check()
        status["health"] = health_data
        
        return status
        
    except Exception as e:
        logger.exception("Detailed status failed")
        raise HTTPException(status_code=500, detail=f"Status generation failed: {str(e)}")


@app.get("/")
async def root() -> Dict[str, str]:
    """Root endpoint with API information."""
    return {
        "service": "Teltubby Health API",
        "version": "1.0.0",
        "endpoints": {
            "health": "/healthz",
            "metrics": "/metrics", 
            "status": "/status"
        },
        "description": "Health monitoring and system status for Teltubby MTProto worker"
    }

