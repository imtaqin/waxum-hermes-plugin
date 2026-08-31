"""HTTP + SSE client for the waxum REST API.

Stdlib-only on purpose (see pyproject.toml — no runtime dependency to pin
or drift out from under Hermes's own environment). Retries with capped
exponential backoff on transport errors and 5xx; 4xx are not retried and
surface as typed exceptions immediately.
"""

from __future__ import annotations

import json
import logging
import random
import time
from typing import Any, Iterator, Optional
from urllib import error as urlerror
from urllib import request as urlrequest

from .config import WaxumConfig
from .exceptions import WaxumAuthError, WaxumRequestError, WaxumSessionUnavailable

logger = logging.getLogger("hermes.plugins.waxum")


class WaxumClient:
    """Synchronous client — callers run it off the event loop via `asyncio.to_thread`."""

    def __init__(self, cfg: WaxumConfig) -> None:
        self.cfg = cfg

    # -- request/response ------------------------------------------------

    def get(self, path: str) -> dict:
        return self._request("GET", path, None)

    def post(self, path: str, body: dict) -> dict:
        return self._request("POST", path, body)

    def _request(self, method: str, path: str, body: Optional[dict]) -> dict:
        url = f"{self.cfg.base_url}{path}"
        data = json.dumps(body).encode() if body is not None else None
        attempt = 0
        while True:
            attempt += 1
            req = urlrequest.Request(url, data=data, headers=self.cfg.headers, method=method)
            try:
                with urlrequest.urlopen(req, timeout=self.cfg.connect_timeout) as resp:
                    raw = resp.read()
                    return json.loads(raw) if raw else {}
            except urlerror.HTTPError as e:
                if e.code == 401:
                    raise WaxumAuthError(f"waxum rejected the bearer token ({method} {path})") from e
                if e.code == 503:
                    raise WaxumSessionUnavailable(f"session {self.cfg.session_id} has no live client") from e
                if e.code < 500 or attempt > self.cfg.max_retries:
                    raise WaxumRequestError(f"{method} {path} -> HTTP {e.code}", status=e.code) from e
                self._sleep_backoff(attempt)
            except urlerror.URLError as e:
                if attempt > self.cfg.max_retries:
                    raise WaxumRequestError(f"{method} {path} -> {e.reason}") from e
                self._sleep_backoff(attempt)

    def _sleep_backoff(self, attempt: int) -> None:
        delay = min(self.cfg.backoff_max, self.cfg.backoff_base * (2 ** (attempt - 1)))
        delay *= 0.5 + random.random()  # jitter, avoid thundering herd on shared waxum instance
        logger.warning("waxum request failed, retrying in %.1fs (attempt %d)", delay, attempt)
        time.sleep(delay)

    # -- SSE event stream --------------------------------------------------

    def stream_events(self, path: str) -> Iterator[dict]:
        """Yields parsed JSON events from a waxum SSE endpoint forever,
        reconnecting with backoff on any transport error. Caller iterates
        this from a background thread and cancels by breaking/GC'ing it.
        """
        attempt = 0
        while True:
            try:
                yield from self._stream_once(path)
                attempt = 0  # clean stream end (server closed) resets backoff
            except (urlerror.URLError, urlerror.HTTPError, TimeoutError, OSError) as e:
                attempt += 1
                logger.warning("waxum event stream dropped (%s), reconnecting", e)
                self._sleep_backoff(attempt)

    def _stream_once(self, path: str) -> Iterator[dict]:
        url = f"{self.cfg.base_url}{path}"
        req = urlrequest.Request(url, headers=self.cfg.headers)
        with urlrequest.urlopen(req, timeout=self.cfg.stream_timeout) as resp:
            buf = ""
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").rstrip("\n")
                if line.startswith("data:"):
                    buf = line[5:].strip()
                elif line == "" and buf:
                    try:
                        yield json.loads(buf)
                    except json.JSONDecodeError:
                        logger.debug("waxum event stream: dropped malformed line: %r", buf)
                    buf = ""

    # -- typed convenience wrappers used by the adapter/tools -------------

    def session_status(self) -> dict:
        return self.get(f"/sessions/{self.cfg.session_id}/status")

    def send_text(self, to: str, text: str, reply_to: Optional[str] = None) -> dict:
        body: dict[str, Any] = {"to": to, "text": text}
        if reply_to:
            body["reply_to"] = reply_to
        return self.post(f"/sessions/{self.cfg.session_id}/messages/text", body)

    def send_buttons(self, to: str, body: str, buttons: list[dict], footer: Optional[str] = None) -> dict:
        # waxum contract: content_text + buttons[{button_id, display_text}]
        payload: dict[str, Any] = {
            "to": to,
            "content_text": body,
            "buttons": [
                {
                    "button_id": b.get("button_id") or b.get("id", ""),
                    "display_text": b.get("display_text") or b.get("text", ""),
                }
                for b in buttons
            ],
        }
        if footer:
            payload["footer"] = footer
        return self.post(f"/sessions/{self.cfg.session_id}/messages/buttons", payload)

    def send_list(
        self, to: str, body: str, button_text: str, sections: list[dict], footer: Optional[str] = None
    ) -> dict:
        # waxum contract: title, description, button_text,
        # sections[{title, rows[{row_id, title, description}]}], footer
        payload: dict[str, Any] = {
            "to": to,
            "title": button_text,
            "description": body,
            "button_text": button_text,
            "sections": [
                {
                    "title": s.get("title", ""),
                    "rows": [
                        {
                            "row_id": r.get("row_id") or r.get("id", ""),
                            "title": r.get("title", ""),
                            "description": r.get("description"),
                        }
                        for r in s.get("rows", [])
                    ],
                }
                for s in sections
            ],
        }
        if footer:
            payload["footer"] = footer
        return self.post(f"/sessions/{self.cfg.session_id}/messages/list", payload)

    def send_quick_reply(self, to: str, body: str, buttons: list[dict]) -> dict:
        # waxum contract: body_text + buttons[{id, display_text}]
        return self.post(f"/sessions/{self.cfg.session_id}/messages/quick-reply", {
            "to": to,
            "body_text": body,
            "buttons": [
                {
                    "id": b.get("id", ""),
                    "display_text": b.get("display_text") or b.get("text", ""),
                }
                for b in buttons
            ],
        })

    def send_cta_url(self, to: str, body: str, button_text: str, url: str, header: Optional[str] = None) -> dict:
        # waxum contract: header_text, body_text, display_text, url, footer_text
        payload: dict[str, Any] = {
            "to": to,
            "header_text": header or button_text,
            "body_text": body,
            "display_text": button_text,
            "url": url,
        }
        return self.post(f"/sessions/{self.cfg.session_id}/messages/cta-url", payload)

    def send_typing(self, to: str) -> None:
        self.post(f"/sessions/{self.cfg.session_id}/chatstate/typing", {"to": to, "state": "composing"})

    def chat_info(self, chat_jid: str) -> dict:
        return self.get(f"/sessions/{self.cfg.session_id}/messages/chat/{chat_jid}")
