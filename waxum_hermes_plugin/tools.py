"""Agent-callable tool handlers.

Per Hermes convention: handlers accept (args: dict, **kwargs), always
return a JSON string (never raise), and never crash the caller's tool
loop even when waxum itself is unreachable.
"""

from __future__ import annotations

import json
import logging
import os
import threading

from .client import WaxumClient
from .config import WaxumConfig
from .exceptions import WaxumError

logger = logging.getLogger("hermes.plugins.waxum")

_client: WaxumClient | None = None
_client_lock = threading.Lock()


def _get_client() -> WaxumClient:
    """Thread-safe lazy singleton — env-only config since tool handlers
    run outside the per-adapter-instance PlatformConfig context.
    """
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = WaxumClient(WaxumConfig.from_platform_config({}))
    return _client


def _error(exc: Exception) -> str:
    logger.error("waxum tool call failed: %s", exc)
    return json.dumps({"success": False, "error": str(exc)})


def send_buttons(args: dict, **kwargs) -> str:
    try:
        result = _get_client().send_buttons(
            to=args["chat_id"],
            body=args["body"],
            buttons=args["buttons"],
            footer=args.get("footer"),
        )
        return json.dumps({"success": True, "message_id": result.get("message_id")})
    except (KeyError, WaxumError) as e:
        return _error(e)


def send_list(args: dict, **kwargs) -> str:
    try:
        result = _get_client().send_list(
            to=args["chat_id"],
            body=args["body"],
            button_text=args["button_text"],
            sections=args["sections"],
            footer=args.get("footer"),
        )
        return json.dumps({"success": True, "message_id": result.get("message_id")})
    except (KeyError, WaxumError) as e:
        return _error(e)


def send_cta_url(args: dict, **kwargs) -> str:
    try:
        result = _get_client().send_cta_url(
            to=args["chat_id"],
            body=args["body"],
            button_text=args["button_text"],
            url=args["url"],
            header=args.get("header"),
        )
        return json.dumps({"success": True, "message_id": result.get("message_id")})
    except (KeyError, WaxumError) as e:
        return _error(e)


_HANDLERS = {
    "waxum_send_buttons": send_buttons,
    "waxum_send_list": send_list,
    "waxum_send_cta_url": send_cta_url,
}


def register_tools(ctx) -> None:
    """Register the outbound client tools on a PluginContext.

    Called two ways:
    - eagerly by Hermes's deferred-platform discovery when ``provides_tools``
      is declared in plugin.yaml (so the tools exist in CLI/TUI sessions too),
    - from ``register()`` in __init__.py when the platform adapter materializes.
    """
    from . import schemas

    for schema in schemas.ALL:
        ctx.register_tool(
            name=schema["name"],
            toolset="waxum",
            schema=schema,
            handler=_HANDLERS[schema["name"]],
            check_fn=lambda: True,
        )
