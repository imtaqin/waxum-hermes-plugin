# waxum-hermes-plugin

Hermes Agent gateway platform adapter for [waxum](https://github.com/imtaqin/waxum)
— gives Hermes real WhatsApp buttons/lists/quick-replies/CTA-URL messages,
which the stock Baileys-based WhatsApp bridge in Hermes cannot do reliably.

The adapter is a thin HTTP client against a waxum instance you already run —
no WhatsApp session logic lives in this plugin.

## Install

1. Have a `waxum` instance running with a session already paired (QR/pair
   code done once via waxum itself).
2. Copy `waxum_platform.py` into `~/.hermes/plugins/`.
3. Set env vars before starting the Hermes gateway:
   ```
   WAXUM_BASE_URL=http://127.0.0.1:3451
   WAXUM_TOKEN=<waxum bearer token>
   WAXUM_SESSION_ID=<waxum session id>
   ```
4. Start Hermes as usual (`hermes gateway` / your normal entrypoint) — the
   plugin registers a `waxum` platform automatically.

## Sending interactive messages

`send()` (the adapter's normal path) only does plain text — that's the
platform-adapter contract. For buttons/lists/CTA-url, call the extra
methods on the adapter instance from a Hermes plugin hook or tool:

```python
await adapter.send_buttons(chat_id, body="Pick one", buttons=[
    {"id": "yes", "text": "Yes"},
    {"id": "no", "text": "No"},
])
await adapter.send_list(chat_id, body="Menu", button_text="Open menu", sections=[...])
await adapter.send_quick_reply(chat_id, body="...", buttons=[...])
await adapter.send_cta_url(chat_id, body="...", button_text="Open", url="https://...")
```

Button/list taps arrive back as normal `MessageEvent`s with
`metadata["waxum_message_type"]` set, so a hook can branch on them.

## Compatibility note

Import paths (`gateway.platforms.base`) follow the publicly documented
`BasePlatformAdapter` contract as of Hermes Agent's developer docs
(nousresearch.com/docs/developer-guide/adding-platform-adapters). If your
installed `hermes-agent` moved that module, only the two `from gateway...`
imports at the top of `waxum_platform.py` need updating — nothing else
changes.

## Endpoints used (waxum side)

`GET /sessions/{id}/status`, `GET /events/tail?session=...&event=message`
(SSE), `POST /sessions/{id}/messages/text|buttons|list|quick-reply|cta-url`,
`POST /sessions/{id}/chatstate/typing`, `GET /sessions/{id}/messages/chat/{jid}`.
All require `Authorization: Bearer <WAXUM_TOKEN>`.
