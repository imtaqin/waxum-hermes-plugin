"""Tool schemas the LLM sees — kept separate from handlers per Hermes convention."""

SEND_BUTTONS = {
    "name": "waxum_send_buttons",
    "description": (
        "Send a WhatsApp message with up to 3 quick-reply buttons via the waxum platform. "
        "Use when you want the user to pick one of a few short options (yes/no, confirm/cancel, "
        "menu choices) instead of typing free text."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "chat_id": {"type": "string", "description": "Destination WhatsApp JID, e.g. 6281234567890@s.whatsapp.net"},
            "body": {"type": "string", "description": "Message body shown above the buttons"},
            "buttons": {
                "type": "array",
                "description": "1-3 buttons",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "Opaque id you receive back when tapped"},
                        "text": {"type": "string", "description": "Visible button label"},
                    },
                    "required": ["id", "text"],
                },
            },
            "footer": {"type": "string", "description": "Optional small footer line"},
        },
        "required": ["chat_id", "body", "buttons"],
    },
}

SEND_LIST = {
    "name": "waxum_send_list",
    "description": (
        "Send a WhatsApp list message via waxum: a single button that opens a scrollable menu "
        "of options grouped into sections. Use for more than 3 choices, where waxum_send_buttons' "
        "3-button cap doesn't fit."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "chat_id": {"type": "string", "description": "Destination WhatsApp JID"},
            "body": {"type": "string", "description": "Message body shown above the menu button"},
            "button_text": {"type": "string", "description": "Label on the button that opens the list"},
            "sections": {
                "type": "array",
                "description": "Grouped menu rows",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "rows": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "title": {"type": "string"},
                                    "description": {"type": "string"},
                                },
                                "required": ["id", "title"],
                            },
                        },
                    },
                    "required": ["title", "rows"],
                },
            },
            "footer": {"type": "string"},
        },
        "required": ["chat_id", "body", "button_text", "sections"],
    },
}

SEND_CTA_URL = {
    "name": "waxum_send_cta_url",
    "description": "Send a WhatsApp message with a single tappable button that opens a URL, via waxum.",
    "parameters": {
        "type": "object",
        "properties": {
            "chat_id": {"type": "string", "description": "Destination WhatsApp JID"},
            "body": {"type": "string"},
            "button_text": {"type": "string", "description": "Label on the URL button"},
            "url": {"type": "string", "description": "URL the button opens"},
            "header": {"type": "string", "description": "Optional header line above the body"},
        },
        "required": ["chat_id", "body", "button_text", "url"],
    },
}

ALL = [SEND_BUTTONS, SEND_LIST, SEND_CTA_URL]
