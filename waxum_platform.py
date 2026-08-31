"""
Hermes Agent platform adapter for waxum (WhatsApp REST gateway).

Drop this file into ~/.hermes/plugins/ (or a subfolder there) to register
"waxum" as a gateway platform, giving Hermes interactive WhatsApp messaging
(buttons, lists, quick-replies, CTA-URL) that the built-in Baileys bridge
cannot do reliably.

Instead of embedding a WhatsApp client in Hermes itself, this adapter is a
thin HTTP client against a waxum instance you already run:
  https://github.com/imtaqin/waxum

Setup
-----
1. `waxum` running and reachable (default http://127.0.0.1:3451), with a
   session already paired (scan QR / pair code once via waxum's own
   console or API — this plugin does not do first-time pairing).
2. Env vars (see PlatformConfig below, surfaced by Hermes as platform
   config keys prefixed WAXUM_):
     WAXUM_BASE_URL     e.g. http://127.0.0.1:3451
     WAXUM_TOKEN        waxum bearer token (Authorization: Bearer <token>)
     WAXUM_SESSION_ID   the waxum session id to bind this platform to
3. `hermes gateway` (or however your Hermes install starts the gateway)
   picks the plugin up automatically from ~/.hermes/plugins/.

Sending interactive messages
-----------------------------
`send()` only sends plain text (the required BasePlatformAdapter contract).
For buttons/lists/CTA-url, call the extra helper methods on the adapter
instance directly from a Hermes tool/plugin hook, e.g.:

    adapter.send_buttons(chat_id, body="Pick one", buttons=[
        {"id": "yes", "text": "Yes"},
        {"id": "no", "text": "No"},
    ])
    adapter.send_list(chat_id, body="Menu", button_text="Open menu", sections=[...])

Receiving button/list taps
---------------------------
waxum delivers a button/list tap as a normal incoming-message webhook event
(message_type reflects the response kind); this adapter forwards it to
Hermes as a regular MessageEvent with `metadata["waxum_message_type"]` set,
so a plugin hook or system prompt can branch on it without a special API.

NOTE: this file targets the documented BasePlatformAdapter contract
(gateway/platforms/base.py: connect/disconnect/send, MessageEvent,
SendResult, ctx.register_platform). Import paths are pinned to what the
public Hermes docs describe — if your installed hermes-agent version
renamed the module, adjust the two `from gateway...` imports below and
nothing else needs to change.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any, Optional
from urllib import error as urlerror
from urllib import request as urlrequest

from gateway.platforms.base import BasePlatformAdapter, MessageEvent, MessageType, SendResult


@dataclass
class WaxumConfig:
    base_url: str
    token: str
    session_id: str
    poll_interval: float = 1.0


class WaxumPlatformAdapter(BasePlatformAdapter):
    """Bridges a waxum WhatsApp session into Hermes's gateway."""

    def __init__(self, cfg: "PlatformConfig") -> None:
        super().__init__(cfg)
        self._waxum = WaxumConfig(
            base_url=(cfg.get("base_url") or os.environ.get("WAXUM_BASE_URL", "http://127.0.0.1:3451")).rstrip("/"),
            token=cfg.get("token") or os.environ["WAXUM_TOKEN"],
            session_id=cfg.get("session_id") or os.environ["WAXUM_SESSION_ID"],
        )
        self._poll_task: Optional[asyncio.Task] = None
        self._seen_message_ids: set[str] = set()

    # -- BasePlatformAdapter contract -----------------------------------

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        status = await self._api_get(f"/sessions/{self._waxum.session_id}/status")
        if not status or status.get("status") != "connected":
            self._mark_disconnected()
            return False
        self._poll_task = asyncio.create_task(self._poll_events())
        self._mark_connected()
        return True

    async def disconnect(self) -> None:
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
        self._mark_disconnected()

    async def send(self, chat_id: str, content: str, reply_to: Optional[str] = None,
                    metadata: Optional[dict] = None) -> SendResult:
        body: dict[str, Any] = {"to": chat_id, "text": content}
        if reply_to:
            body["reply_to"] = reply_to
        return await self._send_message("/messages/text", body)

    async def send_typing(self, chat_id: str) -> None:
        await self._api_post("/chatstate/typing", {"to": chat_id, "state": "composing"})

    async def get_chat_info(self, chat_id: str) -> dict:
        return await self._api_get(f"/messages/chat/{chat_id}") or {}

    # -- Interactive extras (call directly from a plugin hook/tool) -----

    async def send_buttons(self, chat_id: str, body: str, buttons: list[dict],
                            footer: Optional[str] = None) -> SendResult:
        payload = {"to": chat_id, "body": body, "buttons": buttons}
        if footer:
            payload["footer"] = footer
        return await self._send_message("/messages/buttons", payload)

    async def send_list(self, chat_id: str, body: str, button_text: str,
                         sections: list[dict], footer: Optional[str] = None) -> SendResult:
        payload = {
            "to": chat_id,
            "body": body,
            "button_text": button_text,
            "sections": sections,
        }
        if footer:
            payload["footer"] = footer
        return await self._send_message("/messages/list", payload)

    async def send_quick_reply(self, chat_id: str, body: str, buttons: list[dict]) -> SendResult:
        return await self._send_message("/messages/quick-reply", {"to": chat_id, "body": body, "buttons": buttons})

    async def send_cta_url(self, chat_id: str, body: str, button_text: str, url: str,
                            header: Optional[str] = None) -> SendResult:
        payload = {"to": chat_id, "body": body, "button_text": button_text, "url": url}
        if header:
            payload["header"] = header
        return await self._send_message("/messages/cta-url", payload)

    # -- internals --------------------------------------------------------

    async def _poll_events(self) -> None:
        """Long-polls waxum's global SSE event tail, filtered to our session.

        Reconnects on any transport error; a real crash just means the next
        gateway reconnect cycle calls connect() again.
        """
        self._running = True
        while self._running:
            try:
                async for ev in self._sse_stream(
                    f"/events/tail?session={self._waxum.session_id}&event=message"
                ):
                    await self._handle_waxum_event(ev)
            except Exception:
                await asyncio.sleep(self._waxum.poll_interval)

    async def _handle_waxum_event(self, ev: dict) -> None:
        data = ev.get("data") or {}
        msg_id = data.get("message_id")
        if not msg_id or msg_id in self._seen_message_ids or data.get("is_from_me"):
            return
        self._seen_message_ids.add(msg_id)

        source = self.build_source(
            chat_id=data.get("chat"),
            chat_name=data.get("push_name") or data.get("from_phone") or data.get("from"),
            chat_type="group" if data.get("is_group") else "dm",
            user_id=data.get("from"),
            user_name=data.get("push_name"),
        )
        text = data.get("text") or data.get("caption") or ""
        event = MessageEvent(
            text=text,
            message_type=MessageType.TEXT,
            source=source,
            message_id=msg_id,
            metadata={"waxum_message_type": data.get("message_type")},
        )
        await self.handle_message(event)

    async def _send_message(self, path: str, body: dict) -> SendResult:
        result = await self._api_post(f"/sessions/{self._waxum.session_id}{path}", body)
        if result is None:
            return SendResult(success=False, message_id=None, error="waxum request failed")
        return SendResult(success=True, message_id=result.get("message_id"))

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._waxum.token}",
            "Content-Type": "application/json",
        }

    async def _api_get(self, path: str) -> Optional[dict]:
        return await asyncio.to_thread(self._http, "GET", path, None)

    async def _api_post(self, path: str, body: dict) -> Optional[dict]:
        return await asyncio.to_thread(self._http, "POST", path, body)

    def _http(self, method: str, path: str, body: Optional[dict]) -> Optional[dict]:
        url = f"{self._waxum.base_url}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urlrequest.Request(url, data=data, headers=self._headers(), method=method)
        try:
            with urlrequest.urlopen(req, timeout=15) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urlerror.HTTPError as e:
            return None
        except urlerror.URLError:
            return None

    async def _sse_stream(self, path: str):
        """Minimal SSE line reader — no extra dependency for one endpoint."""
        url = f"{self._waxum.base_url}{path}"
        req = urlrequest.Request(url, headers=self._headers())
        resp = await asyncio.to_thread(urlrequest.urlopen, req, None, 300)
        try:
            buf = ""
            while self._running:
                line = await asyncio.to_thread(resp.readline)
                if not line:
                    break
                line = line.decode().rstrip("\n")
                if line.startswith("data:"):
                    buf = line[5:].strip()
                elif line == "" and buf:
                    try:
                        yield json.loads(buf)
                    except json.JSONDecodeError:
                        pass
                    buf = ""
        finally:
            resp.close()


def _check_requirements(cfg: dict) -> bool:
    return bool(cfg.get("token") or os.environ.get("WAXUM_TOKEN")) and \
        bool(cfg.get("session_id") or os.environ.get("WAXUM_SESSION_ID"))


def _validate_config(cfg: dict) -> Optional[str]:
    if not _check_requirements(cfg):
        return "waxum platform needs WAXUM_TOKEN and WAXUM_SESSION_ID (or base_url/token/session_id config keys)"
    return None


def register(ctx) -> None:
    ctx.register_platform(
        name="waxum",
        label="WhatsApp (waxum)",
        adapter_factory=lambda cfg: WaxumPlatformAdapter(cfg),
        check_fn=_check_requirements,
        validate_config=_validate_config,
    )
