"""
Entry point module for teltubby service.

This module wires up configuration, logging, health/metrics, and the Telegram bot
runtime selection (polling vs webhook). It defers most responsibilities to
specialized submodules to keep the entry relatively small and readable.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from typing import Optional


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
    from .web.health import start_health_server
    from .bot.service import TeltubbyBotService

    config = AppConfig.from_env()
    setup_logging(config)

    logger = logging.getLogger("teltubby")
    logger.info("starting teltubby", extra={"mode": config.telegram_mode})

    # Start health/metrics server
    health_runner = await start_health_server(config)

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

    await stop_event.wait()
    await bot.stop()
    await health_runner.cleanup()


def main() -> None:
    _ensure_event_loop_policy()
    try:
        asyncio.run(_async_main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

