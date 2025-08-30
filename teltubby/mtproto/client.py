"""MTProto client wrapper using Telethon.

Responsibilities:
- Manage Telethon client lifecycle and session persistence
- Provide login flow with phone and (optionally) code/password hooks
- Download files by Message reference with progress callbacks

Notes:
- Interactive 2FA/code input is NOT implemented here; the worker will
  surface requests via logs/bot-admin flows in later phases.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

from ..runtime.config import AppConfig


logger = logging.getLogger("teltubby.mtproto")


ProgressCb = Callable[[int, int], Awaitable[None]]  # (current:int, total:int)


@dataclass
class MTAuthHooks:
    """Hooks for interactive authentication steps.

    Fields:
    - request_code: Callable[[], Awaitable[str]] - returns login code
    - request_password: Callable[[], Awaitable[str]] - returns 2FA password
    """

    request_code: Optional[Callable[[], Awaitable[str]]] = None
    request_password: Optional[Callable[[], Awaitable[str]]] = None


class MTProtoClient:
    """Thin wrapper around Telethon's TelegramClient.

    Attributes:
    - _client: TelegramClient | None - underlying client instance
    - _hooks: MTAuthHooks - auth hooks for login challenges
    """

    def __init__(self, config: AppConfig, hooks: Optional[MTAuthHooks] = None) -> None:
        self._cfg = config
        self._hooks = hooks or MTAuthHooks()
        self._client: Optional[TelegramClient] = None

    async def start(self) -> None:
        """Create and start the Telethon client, attempting login if needed."""
        assert self._cfg.mtproto_api_id and self._cfg.mtproto_api_hash
        session_path = self._cfg.mtproto_session_path or "/data/mtproto.session"
        self._client = TelegramClient(session_path, self._cfg.mtproto_api_id, self._cfg.mtproto_api_hash)
        await self._client.connect()

        if not await self._client.is_user_authorized():
            if not self._cfg.mtproto_phone_number:
                raise RuntimeError("MTProto phone number not configured")
            sent = await self._client.send_code_request(self._cfg.mtproto_phone_number)
            code = None
            if self._hooks.request_code:
                code = await self._hooks.request_code()
            else:
                raise RuntimeError("Login code required but no hook provided")
            try:
                await self._client.sign_in(self._cfg.mtproto_phone_number, code, phone_code_hash=sent.phone_code_hash)
            except SessionPasswordNeededError:
                if not self._hooks.request_password:
                    raise RuntimeError("2FA password required but no hook provided")
                pwd = await self._hooks.request_password()
                await self._client.sign_in(password=pwd)

        me = await self._client.get_me()
        logger.info("MTProto client started", extra={"user": getattr(me, "username", None)})

    async def stop(self) -> None:
        """Disconnect the Telethon client."""
        if self._client:
            await self._client.disconnect()
            self._client = None

    async def download_file_by_link(
        self,
        msg_link: str,
        dest_path: str,
        on_progress: Optional[ProgressCb] = None,
    ) -> None:
        """Download a file referenced by a message link to a local path.

        Parameters:
        - msg_link: str - t.me/c/... or public message link
        - dest_path: str - filesystem path to write
        - on_progress: Optional[ProgressCb] - async progress callback
        """
        assert self._client
        from telethon.tl.functions.messages import ImportChatInviteRequest  # lazy import
        from telethon.tl.functions.messages import GetMessagesRequest
        from telethon.utils import parse_username

        # NOTE: Full link parsing is complex; Phase 2 will focus on file_id-based
        # downloads via job payload and chat/message ids resolved by worker. This
        # method is kept for future completeness.
        raise NotImplementedError("Link-based download not implemented in Phase 2")


