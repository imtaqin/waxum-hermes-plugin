"""In-session slash command: /waxum-status"""

from __future__ import annotations

from .exceptions import WaxumError


def waxum_status(raw_args: str) -> str:
    try:
        from .tools import _get_client  # reuse the lazy singleton

        client = _get_client()
        status = client.session_status()
        return (
            f"waxum session {client.cfg.session_id}: "
            f"status={status.get('status')} reachability={status.get('reachability')}"
        )
    except WaxumError as e:
        return f"waxum status check failed: {e}"
