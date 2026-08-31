"""BasePlatformAdapter implementation — wires a waxum session into Hermes's gateway."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any, Optional

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


def _normalise_phone(value: Any) -> str | None:
    if value is None:
        return None
    return str(value).split("@", 1)[0].split(":", 1)[0]


def _interactive_command(body: str | None) -> str | None:
    """Map Waxum's self-chat interactive echo back to a gateway command."""
    if not body:
        return None
    labels = {
        "🤖 Ganti Model": "/model",
        "🤖 Model": "/model",
        "📋 Menu": "/menu",
        "📊 Status": "/status",
        "🔌 Waxum Status": "/waxum-status",
        "🔄 Reset": "/reset",
        "🎙️ Voice Mode": "/voice on",
        "🔇 Voice Off": "/voice off",
        "📦 Compress": "/compress",
        "🧠 Memory": "/memory",
        "📋 Context Info": "/ctx",
        "❓ Help": "/help",
    }
    return labels.get(body.strip())


def history_message_to_event_data(message: dict) -> dict | None:
    """Convert Waxum history rows to the adapter's event-data shape.

    The Waxum SSE endpoint intentionally exposes only a 160-character preview.
    History rows contain the complete body and are therefore used to recover
    messages that SSE cannot represent, including self-chat interactive replies.
    """
    direction = message.get("direction")
    msg_type = message.get("msg_type") or message.get("message_type")
    body = message.get("body")
    is_interactive_response = msg_type in {"interactive_response", "buttons_response", "list_response"}
    command = _interactive_command(body) if is_interactive_response else None
    # An interactive response is a user action only in the configured
    # self-chat. Outgoing button replies sent to other chats must not be
    # replayed into Hermes as commands.
    chat = message.get("chat_jid") or message.get("chat")
    sender = message.get("sender_jid") or message.get("from") or chat
    is_self_chat = bool(chat and sender and str(chat) == str(sender))
    if direction == "out" and (not command or not is_self_chat):
        return None
    if not body and not command:
        return None
    chat = message.get("chat_jid") or message.get("chat")
    sender = message.get("sender_jid") or message.get("from") or chat
    if not chat or not sender or not message.get("message_id"):
        return None
    return {
        "message_id": message["message_id"],
        "chat": chat,
        "from": sender,
        "from_phone": _normalise_phone(sender),
        "push_name": message.get("push_name"),
        "text": command or body or "",
        "caption": None,
        "message_type": msg_type,
        "is_group": str(chat).endswith("@g.us"),
        # Self-chat interactive responses are user actions, despite Waxum
        # storing them as outgoing rows because the bot account sent them.
        "is_from_me": False,
    }


def _preview_to_event_data(event: dict) -> dict | None:
    """Best-effort parser for old SSE previews; never invents message data."""
    preview = event.get("payload_preview")
    if not isinstance(preview, str):
        return None
    try:
        payload = json.loads(preview)
    except (TypeError, ValueError):
        return None
    return payload.get("data") if isinstance(payload, dict) else None


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
        self._seen_lock = threading.Lock()
        self._history_thread: Optional[threading.Thread] = None
        self._history_initialized = False

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
        # Establish the history watermark before starting live consumers. This
        # prevents a message arriving during startup from being mistaken for
        # an old row and skipped by the initial poll.
        try:
            page = await asyncio.to_thread(self.client.list_messages, 50)
            for message in page.get("messages", []) if isinstance(page, dict) else []:
                data = history_message_to_event_data(message)
                if data and data.get("message_id"):
                    self._already_seen(data["message_id"])
            self._history_initialized = True
        except WaxumError as exc:
            logger.warning("waxum history seed failed; poller will retry: %s", exc)

        self._stream_thread = threading.Thread(
            target=self._run_stream, name=f"waxum-events-{self.waxum_config.session_id}", daemon=True,
        )
        self._stream_thread.start()
        self._history_thread = threading.Thread(
            target=self._run_history_poll,
            name=f"waxum-history-{self.waxum_config.session_id}",
            daemon=True,
        )
        self._history_thread.start()
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
                data = _preview_to_event_data(ev)
                if not data:
                    continue
                msg_id = data.get("message_id")
                if msg_id and self._already_seen(msg_id):
                    continue
                self._loop.call_soon_threadsafe(
                    asyncio.ensure_future,
                    self._handle_data(data, claimed=True),
                )

    def _run_history_poll(self) -> None:
        """Recover complete messages unavailable in Waxum's SSE preview.

        The API returns newest-first pages. We repeatedly read the head and
        deduplicate by WhatsApp message ID, avoiding the unstable arrival
        cursor semantics documented by Waxum's own chat store.
        """
        while not self._stop.is_set():
            try:
                page = self.client.list_messages(limit=50)
                messages = page.get("messages", []) if isinstance(page, dict) else []
                if not self._history_initialized:
                    # Do not replay the last 50 historical messages on every
                    # gateway restart. The next pass is the live recovery
                    # boundary; SSE remains responsible for messages already
                    # observed during this first read.
                    for message in messages:
                        data = history_message_to_event_data(message)
                        if data and data.get("message_id"):
                            self._already_seen(data["message_id"])
                    self._history_initialized = True
                else:
                    for message in reversed(messages):
                        data = history_message_to_event_data(message)
                        if not data:
                            continue
                        msg_id = data.get("message_id")
                        if not msg_id or self._already_seen(msg_id):
                            continue
                        if self._loop and self._loop.is_running():
                            self._loop.call_soon_threadsafe(
                                asyncio.ensure_future,
                                self._handle_data(data, claimed=True),
                            )
            except WaxumError as exc:
                logger.warning("waxum history poll failed: %s", exc)
            self._stop.wait(2.0)

    async def _handle_event(self, ev: dict) -> None:
        data = ev.get("data") or _preview_to_event_data(ev)
        if data:
            msg_id = data.get("message_id")
            if msg_id and self._already_seen(msg_id):
                return
            await self._handle_data(data, claimed=True)

    async def _handle_data(self, data: dict, claimed: bool = False) -> None:
        msg_id = data.get("message_id")
        if not msg_id or data.get("is_from_me") or (not claimed and self._already_seen(msg_id)):
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
        with self._seen_lock:
            if msg_id in self._seen_set:
                return True
            self._seen_set.add(msg_id)
            self._seen_ids.append(msg_id)
            if len(self._seen_ids) > _DEDUPE_MAX:
                stale = self._seen_ids.pop(0)
                self._seen_set.discard(stale)
            return False
