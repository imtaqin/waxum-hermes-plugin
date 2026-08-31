# waxum-hermes-plugin

A [Hermes Agent](https://github.com/NousResearch/hermes-agent) gateway
platform plugin backed by [waxum](https://github.com/imtaqin/waxum). Gives
Hermes real WhatsApp interactivity — buttons, list menus, CTA-url — that the
stock Baileys-based WhatsApp bridge can't do reliably.

Structured like Hermes's own platform-adapter plugins: a `plugin.yaml`
manifest, a `register(ctx)` entry point, registered agent tools (the LLM can
send buttons/lists itself, not just plain text), a slash command, typed
config/exceptions, retrying HTTP client with backoff, and unit tests.

```
waxum_hermes_plugin/
├── plugin.yaml       # manifest: capabilities, required env, config schema
├── __init__.py        # register(ctx) — platform + tools + slash command
├── adapter.py          # BasePlatformAdapter: connect/disconnect/send + event stream
├── client.py            # HTTP + SSE client: retries, backoff, typed errors
├── config.py              # WaxumConfig, validated from PlatformConfig or env
├── schemas.py              # tool schemas the LLM sees
├── tools.py                 # tool handlers (agent-callable send_buttons/list/cta_url)
└── commands.py                # /waxum-status slash command
tests/                          # unit tests (client retry/backoff, config validation)
```

## Install

**Option A — pip (recommended, auto-discovered via entry point):**
```bash
pip install /path/to/waxum-hermes-plugin
```
Hermes picks it up on next start via the `hermes_agent.plugins` entry point
in `pyproject.toml` — no manual file copying.

**Option B — manual drop-in:**
```bash
cp -r waxum_hermes_plugin ~/.hermes/plugins/waxum
```

Either way, set the required env vars before starting the gateway:
```
WAXUM_BASE_URL=http://127.0.0.1:3451
WAXUM_TOKEN=<waxum bearer token>
WAXUM_SESSION_ID=<waxum session id, already paired via waxum itself>
```

Verify with Hermes's own plugin linter:
```bash
hermes plugins doctor . --ci
```

## What it registers

- **Platform `waxum`** — a live WhatsApp session. Connects by polling
  `GET /sessions/{id}/status`, then streams incoming messages from waxum's
  `GET /events/tail` SSE endpoint on a background thread (auto-reconnects
  with jittered exponential backoff on any drop).
- **Tools** `waxum_send_buttons`, `waxum_send_list`, `waxum_send_cta_url` —
  the LLM can call these directly mid-conversation to send interactive
  WhatsApp messages, no separate plugin hook required.
- **Command** `/waxum-status` — prints the session's connection status.

Button/list taps come back as ordinary incoming `MessageEvent`s with
`metadata["waxum_message_type"]` set (e.g. `buttons_response`,
`list_response`), so normal conversation handling sees them without a
special code path.

## Reliability notes

- HTTP calls retry on 5xx/transport errors with capped exponential backoff
  + jitter; 401 and 503 (session not connected) fail fast with a typed
  exception instead of retrying — the retriable/non-retriable split matches
  waxum's own status codes.
- The SSE stream runs on its own thread and reconnects indefinitely; a
  bounded in-memory id set (see `adapter.py`) drops duplicate deliveries
  across reconnects.
- Tool handlers never raise — every waxum error becomes a JSON
  `{"success": false, "error": ...}` string, per Hermes's tool-handler
  contract.

## Testing

```bash
python -m unittest discover -s tests
```
No network or live waxum instance required — the client tests mock
`urllib` directly.

## Compatibility

Targets the documented `BasePlatformAdapter` contract
(`gateway.platforms.base`) and `PluginContext` API as of the public Hermes
Agent developer docs. `adapter.py` is the only file that imports Hermes's
own `gateway` package, and only inside `register()` — everything else
(`config.py`, `client.py`, `tools.py`) is plain stdlib Python you can test
and reuse without a Hermes install.
