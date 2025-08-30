"""Health and metrics server.

Exposes `/healthz` and `/metrics` on the configured port. Metrics are
provided using `prometheus_client` and include counters and gauges needed
for MVP. The server runs within the same event loop as the bot.
"""

from __future__ import annotations

import logging

from aiohttp import web
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from ..runtime.config import AppConfig


logger = logging.getLogger("teltubby.health")

# Global registry (default) is fine for MVP


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def handle_metrics(request: web.Request) -> web.Response:
    from prometheus_client import REGISTRY

    output = generate_latest(REGISTRY)
    # Use only the MIME type part, not the full content-type with charset
    content_type = CONTENT_TYPE_LATEST.split(';')[0]
    return web.Response(body=output, content_type=content_type)


async def start_health_server(config: AppConfig) -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/healthz", handle_health)
    app.router.add_get("/metrics", handle_metrics)

    runner = web.AppRunner(app)
    await runner.setup()
    bind_host = "127.0.0.1" if config.bind_health_localhost_only else "0.0.0.0"
    site = web.TCPSite(runner, bind_host, config.health_port)
    await site.start()
    logger.info(
        "health server started", 
        extra={"host": bind_host, "port": config.health_port}
    )
    return runner

