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
        self._client = TelegramClient(
            session_path, 
            self._cfg.mtproto_api_id, 
            self._cfg.mtproto_api_hash
        )
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
                await self._client.sign_in(
                    self._cfg.mtproto_phone_number, 
                    code, 
                    phone_code_hash=sent.phone_code_hash
                )
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

    async def download_file_by_message(
        self,
        chat_id: int,
        message_id: int,
        dest_path: str,
        on_progress: Optional[ProgressCb] = None,
    ) -> int:
        """Download a file from a specific message to a local path.

        Parameters:
        - chat_id: int - Telegram chat ID
        - message_id: int - Telegram message ID
        - dest_path: str - filesystem path to write
        - on_progress: Optional[ProgressCb] - async progress callback

        Returns:
        - int: Size of downloaded file in bytes

        Raises:
        - RuntimeError: If client not connected or message not found
        - ValueError: If message has no media
        """
        if not self._client:
            raise RuntimeError("MTProto client not connected")

        try:
            # Get the message by chat_id and message_id
            message = await self._client.get_messages(chat_id, ids=message_id)
            
            if not message:
                raise ValueError(f"Message {message_id} not found in chat {chat_id}")
            
            if not message.media:
                raise ValueError(f"Message {message_id} has no media content")
            
            # Download the media file
            logger.info(f"Downloading media from message {message_id} in chat {chat_id}")
            
            # Use Telethon's download_media method with progress callback
            if on_progress:
                # Create a progress callback wrapper
                async def progress_callback(received_bytes: int, total_bytes: int):
                    await on_progress(received_bytes, total_bytes)
                
                downloaded_path = await self._client.download_media(
                    message.media,
                    dest_path,
                    progress_callback=progress_callback
                )
            else:
                downloaded_path = await self._client.download_media(
                    message.media,
                    dest_path
                )
            
            # Verify the file was downloaded and get its size
            import os
            if os.path.exists(downloaded_path):
                file_size = os.path.getsize(downloaded_path)
                logger.info(f"Successfully downloaded {file_size} bytes to {downloaded_path}")
                return file_size
            else:
                raise RuntimeError(f"Download completed but file not found at {downloaded_path}")
                
        except Exception as e:
            logger.error(f"Failed to download file from message {message_id}: {e}")
            raise

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
        # NOTE: Full link parsing is complex; Phase 2 will focus on file_id-based
        # downloads via job payload and chat/message ids resolved by worker. This
        # method is kept for future completeness.
        raise NotImplementedError("Link-based download not implemented in Phase 2")


