"""
Entry point module for teltubby service.

This module wires up configuration, logging, health/metrics, and the Telegram bot
runtime selection (polling vs webhook). Enhanced with comprehensive health monitoring
and MTProto worker status tracking.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from typing import Optional

import uvicorn
from fastapi import FastAPI


def _ensure_event_loop_policy() -> None:
    """Install uvloop if available for better performance."""
    try:
        import uvloop  # type: ignore

        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    except Exception:  # pragma: no cover - fallback to default loop
        pass


async def _async_main() -> None:
    """Async entry point that sets up services and starts the bot."""
    from .runtime.config import AppConfig
    from .runtime.logging_setup import setup_logging
    from .web.health import app as health_app
    from .bot.service import TeltubbyBotService

    config = AppConfig.from_env()
    setup_logging(config)

    logger = logging.getLogger("teltubby")
    logger.info("starting teltubby", extra={"mode": config.telegram_mode})

    # Start health monitoring server in background
    health_server = uvicorn.Server(
        config=uvicorn.Config(
            app=health_app,
            host="127.0.0.1" if config.bind_health_localhost_only else "0.0.0.0",
            port=config.health_port,
            log_level="info",
            access_log=False
        )
    )
    
    # Start health server in background task
    health_task = asyncio.create_task(health_server.serve())
    logger.info("health monitoring server started", extra={"port": config.health_port})

    # Start bot service
    bot = TeltubbyBotService(config)
    await bot.start()

    # Graceful shutdown signals
    stop_event = asyncio.Event()

    def _handle_signal(signame: str) -> None:
        logger.warning("received signal, stopping", extra={"signal": signame})
        stop_event.set()

    loop = asyncio.get_running_loop()
    for signame in ("SIGINT", "SIGTERM"):
        if hasattr(signal, signame):
            loop.add_signal_handler(getattr(signal, signame), _handle_signal, signame)

    try:
        await stop_event.wait()
    finally:
        # Cleanup
        logger.info("shutting down services")
        await bot.stop()
        health_task.cancel()
        try:
            await health_task
        except asyncio.CancelledError:
            pass
        logger.info("shutdown complete")


def main() -> None:
    _ensure_event_loop_policy()
    try:
        asyncio.run(_async_main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

