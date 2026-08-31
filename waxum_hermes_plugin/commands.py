"""In-session slash commands: /waxum-status, /menu, /model."""

from __future__ import annotations

import json
import logging

from .exceptions import WaxumError

logger = logging.getLogger("hermes.plugins.waxum")

# Commands shown in the menu, grouped by section.
# row_id is the exact text that gets sent back when the user taps —
# it's a real slash command the gateway will execute.
_MENU_SECTIONS = [
    {
        "title": "Sering Dipakai",
        "rows": [
            {"row_id": "/status", "title": "📊 Status", "description": "Lihat model, provider, context"},
            {"row_id": "/reset", "title": "🔄 Reset", "description": "Mulai sesi baru"},
            {"row_id": "/model", "title": "🤖 Ganti Model", "description": "Pilih model LLM"},
            {"row_id": "/voice on", "title": "🎙️ Voice Mode", "description": "Aktifkan voice-to-voice"},
            {"row_id": "/voice off", "title": "🔇 Voice Off", "description": "Matikan voice mode"},
        ],
    },
    {
        "title": "Konteks & Memori",
        "rows": [
            {"row_id": "/compress", "title": "📦 Compress", "description": "Ringkas context"},
            {"row_id": "/memory", "title": "🧠 Memory", "description": "Lihat memori tersimpan"},
            {"row_id": "/ctx", "title": "📋 Context Info", "description": "Info detail context window"},
        ],
    },
    {
        "title": "Lainnya",
        "rows": [
            {"row_id": "/help", "title": "❓ Help", "description": "Semua command tersedia"},
            {"row_id": "/waxum-status", "title": "🔌 Waxum Status", "description": "Status koneksi waxum"},
        ],
    },
]

# Quick-reply buttons for the most common actions (max 3 — WhatsApp limit)
_QUICK_BUTTONS = [
    {"button_id": "/status", "display_text": "📊 Status"},
    {"button_id": "/model", "display_text": "🤖 Model"},
    {"button_id": "/menu", "display_text": "📋 Menu"},
]


def waxum_status(raw_args: str) -> str:
    try:
        from .tools import _get_client

        client = _get_client()
        status = client.session_status()
        return (
            f"waxum session {client.cfg.session_id}: "
            f"status={status.get('status')} reachability={status.get('reachability')}"
        )
    except WaxumError as e:
        return f"waxum status check failed: {e}"


def menu(raw_args: str) -> str:
    """Send an interactive list menu with all common commands.

    Tapping a row sends the row_id back as text — which the gateway
    processes as a slash command.
    """
    try:
        from .tools import _get_client

        client = _get_client()
        result = client.send_list(
            to=_resolve_recipient(raw_args),
            body="Pilih command dari menu di bawah:",
            button_text="📋 Menu",
            sections=_MENU_SECTIONS,
            footer="hermes-waxum gateway",
        )
        mid = result.get("message_id", "?")
        return f"✅ Menu terkirim (id: {mid}). Pilih salah satu untuk eksekusi command."
    except (WaxumError, Exception) as e:
        return f"❌ Gagal kirim menu: {e}"


def model_picker(raw_args: str) -> str:
    """Send quick-reply buttons for model switching.

    Tapping a button sends the command text back.
    """
    try:
        from .tools import _get_client

        client = _get_client()
        result = client.send_buttons(
            to=_resolve_recipient(raw_args),
            body="Pilih aksi cepat:",
            buttons=_QUICK_BUTTONS,
            footer="hermes-waxum gateway",
        )
        mid = result.get("message_id", "?")
        return f"✅ Tombol cepat terkirim (id: {mid}). Tap untuk eksekusi."
    except (WaxumError, Exception) as e:
        return f"❌ Gagal kirim tombol: {e}"


def _resolve_recipient(raw_args: str) -> str:
    """Extract the recipient JID/phone from command args.

    /menu 6285117822731  → 6285117822731
    /menu                → falls back to env WAXUM_DEFAULT_RECIPIENT or
                           the session's own phone (self-chat).
    """
    import os

    if raw_args and raw_args.strip():
        return raw_args.strip().split()[0]
    fallback = os.environ.get("WAXUM_DEFAULT_RECIPIENT")
    if fallback:
        return fallback
    # Last resort: try the session's own phone (self-chat)
    try:
        from .tools import _get_client

        client = _get_client()
        status = client.session_status()
        phone = status.get("phone_number")
        if phone:
            return phone
    except Exception:
        pass
    raise WaxumError(
        "no recipient specified — usage: /menu <phone> or set WAXUM_DEFAULT_RECIPIENT"
    )
