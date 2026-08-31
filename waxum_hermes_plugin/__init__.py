"""waxum platform plugin for Hermes Agent.

Registers:
  - a "waxum" gateway platform (real WhatsApp session via waxum's REST API)
  - three agent tools (waxum_send_buttons / _list / _cta_url) so the LLM
    itself can send interactive WhatsApp messages, not just plain text
  - a /waxum-status slash command

See README.md for install (pip entry point or manual ~/.hermes/plugins/ drop-in).
"""

from __future__ import annotations

from . import commands, schemas, tools
from .config import check_requirements, validate_config

__all__ = ["register"]

_TOOL_HANDLERS = {
    "waxum_send_buttons": tools.send_buttons,
    "waxum_send_list": tools.send_list,
    "waxum_send_cta_url": tools.send_cta_url,
}


def register(ctx) -> None:
    # Imported lazily: adapter.py depends on Hermes's own `gateway` package,
    # which only exists inside a real Hermes install — keep the rest of this
    # package (config/client/tools, and its unit tests) importable without it.
    from .adapter import WaxumPlatformAdapter

    ctx.register_platform(
        name="waxum",
        label="WhatsApp (waxum)",
        adapter_factory=lambda cfg: WaxumPlatformAdapter(cfg),
        check_fn=check_requirements,
        validate_config=validate_config,
    )

    for schema in schemas.ALL:
        ctx.register_tool(
            name=schema["name"],
            toolset="waxum",
            schema=schema,
            handler=_TOOL_HANDLERS[schema["name"]],
            check_fn=lambda: check_requirements({}),
        )

    ctx.register_command(
        name="waxum-status",
        handler=commands.waxum_status,
        description="Show the connection status of the waxum WhatsApp session",
    )
    ctx.register_command(
        name="menu",
        handler=commands.menu,
        description="Send an interactive WhatsApp list menu with all common commands (tap to execute)",
        args_hint="[phone]",
    )
