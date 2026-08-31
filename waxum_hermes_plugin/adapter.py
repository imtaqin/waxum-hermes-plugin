"""BasePlatformAdapter implementation — wires a waxum session into Hermes's gateway."""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Optional

from gateway.config import Platform
from gateway.platforms.base import BasePlatformAdapter, MessageEvent, MessageType, SendResult

from .client import WaxumClient
from .config import WaxumConfig
from .exceptions import WaxumError, WaxumSessionUnavailable

logger = logging.getLogger("hermes.plugins.waxum")

# ponytail: unbounded in-memory set, fine for the volume one WhatsApp
# session sees; swap for an LRU/TTL cache if this ever needs to survive
# millions of messages per process lifetime.
_DEDUPE_MAX = 10_000


class WaxumPlatformAdapter(BasePlatformAdapter):
    """Bridges one waxum WhatsApp session into Hermes as a gateway platform."""

    def __init__(self, cfg) -> None:
        super().__init__(cfg, Platform("waxum"))
        self.waxum_config = WaxumConfig.from_platform_config(cfg)
        self.client = WaxumClient(self.waxum_config)
        self._stream_thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop = threading.Event()
        self._seen_ids: list[str] = []
        self._seen_set: set[str] = set()

    # -- BasePlatformAdapter contract -------------------------------------

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        try:
            status = await asyncio.to_thread(self.client.session_status)
        except WaxumError as e:
            logger.error("waxum connect failed: %s", e)
            self._mark_disconnected()
            return False

        # waxum's SessionStatus enum: disconnected, connecting,
        # waiting_for_qr, waiting_for_pair_code, connected, logged_in.
        # A paired session reports "logged_in" once the socket is alive —
        # both "connected" and "logged_in" mean usable.
        if status.get("status") not in ("connected", "logged_in") or not status.get("is_logged_in"):
            logger.warning(
                "waxum session %s is not connected (status=%s) — pair it via waxum first",
                self.waxum_config.session_id, status.get("status"),
            )
            self._mark_disconnected()
            return False

        self._stop.clear()
        self._loop = asyncio.get_running_loop()
        self._stream_thread = threading.Thread(
            target=self._run_stream, name=f"waxum-events-{self.waxum_config.session_id}", daemon=True,
        )
        self._stream_thread.start()
        self._mark_connected()
        logger.info("waxum adapter connected (session=%s)", self.waxum_config.session_id)
        return True

    async def disconnect(self) -> None:
        self._stop.set()
        self._mark_disconnected()

    async def send(self, chat_id: str, content: str, reply_to: Optional[str] = None,
                    metadata: Optional[dict] = None) -> SendResult:
        return await self._call(self.client.send_text, chat_id, content, reply_to)

    async def send_typing(self, chat_id: str) -> None:
        try:
            await asyncio.to_thread(self.client.send_typing, chat_id)
        except WaxumError as e:
            logger.debug("waxum send_typing failed (non-fatal): %s", e)

    async def get_chat_info(self, chat_id: str) -> dict:
        try:
            return await asyncio.to_thread(self.client.chat_info, chat_id)
        except WaxumError as e:
            logger.debug("waxum get_chat_info failed: %s", e)
            return {}

    # -- interactive sends, also exposed as agent tools in tools.py -------

    async def send_buttons(self, chat_id: str, body: str, buttons: list[dict],
                            footer: Optional[str] = None) -> SendResult:
        return await self._call(self.client.send_buttons, chat_id, body, buttons, footer)

    async def send_list(self, chat_id: str, body: str, button_text: str,
                         sections: list[dict], footer: Optional[str] = None) -> SendResult:
        return await self._call(self.client.send_list, chat_id, body, button_text, sections, footer)

    async def send_quick_reply(self, chat_id: str, body: str, buttons: list[dict]) -> SendResult:
        return await self._call(self.client.send_quick_reply, chat_id, body, buttons)

    async def send_cta_url(self, chat_id: str, body: str, button_text: str, url: str,
                            header: Optional[str] = None) -> SendResult:
        return await self._call(self.client.send_cta_url, chat_id, body, button_text, url, header)

    async def _call(self, fn, *args) -> SendResult:
        try:
            result = await asyncio.to_thread(fn, *args)
            return SendResult(success=True, message_id=result.get("message_id"))
        except WaxumSessionUnavailable as e:
            return SendResult(success=False, message_id=None, error=str(e))
        except WaxumError as e:
            logger.error("waxum send failed: %s", e)
            return SendResult(success=False, message_id=None, error=str(e))

    # -- event stream (runs on a dedicated thread; hands events back to
    #    the adapter's asyncio loop via call_soon_threadsafe) ------------

    def _run_stream(self) -> None:
        path = f"/events/tail?session={self.waxum_config.session_id}&event=message"
        for ev in self.client.stream_events(path):
            if self._stop.is_set():
                return
            if self._loop and self._loop.is_running():
                self._loop.call_soon_threadsafe(asyncio.ensure_future, self._handle_event(ev))

    async def _handle_event(self, ev: dict) -> None:
        data = ev.get("data") or {}
        msg_id = data.get("message_id")
        if not msg_id or data.get("is_from_me") or self._already_seen(msg_id):
            return

        source = self.build_source(
            chat_id=data.get("chat"),
            chat_name=data.get("push_name") or data.get("from_phone") or data.get("from"),
            chat_type="group" if data.get("is_group") else "dm",
            user_id=data.get("from"),
            user_name=data.get("push_name"),
        )
        event = MessageEvent(
            text=data.get("text") or data.get("caption") or "",
            message_type=MessageType.TEXT,
            source=source,
            message_id=msg_id,
            metadata={
                "waxum_message_type": data.get("message_type"),
                "waxum_from_phone": data.get("from_phone"),
            },
        )
        try:
            await self.handle_message(event)
        except Exception:
            logger.exception("waxum adapter: error handling incoming message %s", msg_id)

    def _already_seen(self, msg_id: str) -> bool:
        if msg_id in self._seen_set:
            return True
        self._seen_set.add(msg_id)
        self._seen_ids.append(msg_id)
        if len(self._seen_ids) > _DEDUPE_MAX:
            stale = self._seen_ids.pop(0)
            self._seen_set.discard(stale)
        return False
