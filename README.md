# Claude Gateway

Claude Gateway exposes local HTTP endpoints compatible with OpenAI Chat Completions and Anthropic Messages, backed by Claude Code subprocess execution.

## Features

- OpenAI-compatible `POST /v1/chat/completions`
- Anthropic-compatible `POST /v1/messages`
- Non-streaming and SSE streaming responses
- Claude Code subprocess backend using `claude -p --output-format stream-json`
- Optional Claude Code permission bypass via `CLAUDE_GATEWAY_CLAUDE_ARGS`
- Bearer-token API authentication
- Lightweight session persistence under `.sessions/`
- Session continuity via `X-Claude-Gateway-Session-Id`, `X-Hermes-Session-Id`, or body `session_id`
- Cache usage metadata passthrough from Claude Code `stream-json` result events

## Requirements

- Python 3.11+
- Claude Code CLI available as `claude` on `PATH`
- A configured Claude Code login/session on the machine running the gateway

## Run from source

```bash
git clone https://github.com/fengyizhu/claude-agent.git
cd claude-agent

python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e '.[dev]'

CLAUDE_GATEWAY_API_KEY=dev-secret \
CLAUDE_GATEWAY_CLAUDE_ARGS='--dangerously-skip-permissions' \
python -m claude_gateway.server
```

The server listens on:

```text
http://127.0.0.1:8765
```

You can also use the helper script:

```bash
CLAUDE_GATEWAY_API_KEY=dev-secret scripts/dev_server.sh
```

## Configuration

| Environment variable | Default | Description |
| --- | --- | --- |
| `CLAUDE_GATEWAY_HOST` | `127.0.0.1` | HTTP bind host |
| `CLAUDE_GATEWAY_PORT` | `8765` | HTTP bind port |
| `CLAUDE_GATEWAY_API_KEY` | unset | Bearer token required by API requests |
| `CLAUDE_GATEWAY_ALLOW_NO_AUTH` | `false` | Allow running without API key for local tests only |
| `CLAUDE_GATEWAY_MODEL_NAME` | `claude-code` | Model name advertised by `/v1/models` |
| `CLAUDE_GATEWAY_CLAUDE_BIN` | `claude` | Claude Code executable |
| `CLAUDE_GATEWAY_CLAUDE_ARGS` | unset | Extra Claude Code args, e.g. `--dangerously-skip-permissions` |
| `CLAUDE_GATEWAY_WORKDIR` | current directory | Working directory for Claude Code subprocesses |
| `CLAUDE_GATEWAY_SESSIONS_DIR` | `<workdir>/.sessions` | Session store directory |
| `CLAUDE_GATEWAY_REQUEST_TIMEOUT_SECONDS` | `1800` | Per-run timeout |
| `CLAUDE_GATEWAY_MAX_CONCURRENT_RUNS` | `4` | Max concurrent Claude Code subprocesses |
| `CLAUDE_GATEWAY_CORS_ORIGINS` | unset | Comma-separated browser origins |

## API examples

### Health

```bash
curl -sS http://127.0.0.1:8765/health
```

### OpenAI Chat Completions

```bash
curl -sS http://127.0.0.1:8765/v1/chat/completions \
  -H "Authorization: Bearer dev-secret" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-code",
    "messages": [
      {"role": "user", "content": "Say hello from Claude Gateway"}
    ],
    "stream": false
  }'
```

Streaming:

```bash
curl -N http://127.0.0.1:8765/v1/chat/completions \
  -H "Authorization: Bearer dev-secret" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-code",
    "messages": [
      {"role": "user", "content": "Stream three short sentences"}
    ],
    "stream": true
  }'
```

### Anthropic Messages

```bash
curl -sS http://127.0.0.1:8765/v1/messages \
  -H "Authorization: Bearer dev-secret" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-code",
    "max_tokens": 1024,
    "messages": [
      {"role": "user", "content": "Say hello using Anthropic Messages format"}
    ],
    "stream": false
  }'
```

Streaming:

```bash
curl -N http://127.0.0.1:8765/v1/messages \
  -H "Authorization: Bearer dev-secret" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-code",
    "max_tokens": 1024,
    "messages": [
      {"role": "user", "content": "Stream via Anthropic Messages SSE"}
    ],
    "stream": true
  }'
```

## Session continuity

Pass the same session id on every request:

```http
X-Claude-Gateway-Session-Id: demo-session
```

The gateway also accepts:

```http
X-Hermes-Session-Id: demo-session
```

or body-level:

```json
{
  "session_id": "demo-session",
  "messages": [
    {"role": "user", "content": "Continue this session"}
  ]
}
```

Priority:

```text
X-Claude-Gateway-Session-Id
> X-Hermes-Session-Id
> body.session_id
> derived id from system prompt + first user message
```

By default, the gateway restores prior turns from `.sessions/<session-id>.json` and injects them into the next prompt. You can control this with:

```json
{
  "session": {
    "mode": "resume",
    "max_history_messages": 40
  },
  "messages": [
    {"role": "user", "content": "Continue"}
  ]
}
```

Supported modes:

- `resume` — restore stored history, default
- `stateless` — do not restore stored history for this request
- `reset` — delete stored history before running this request

## Cache metadata

Claude Code `stream-json` result usage is surfaced in `claude_gateway.cache`, for example:

```json
{
  "claude_gateway": {
    "cache": {
      "cache_creation_input_tokens": 0,
      "cache_read_input_tokens": 13312,
      "cache_hit_input_tokens": 13312,
      "cache_miss_input_tokens": 0,
      "cache_creation_ephemeral_5m_input_tokens": 0,
      "cache_creation_ephemeral_1h_input_tokens": 0
    }
  }
}
```

## Tests

```bash
source .venv/bin/activate
python -m pytest -q
```

## Smoke scripts

```bash
scripts/test_chat_completions.sh
scripts/test_streaming.sh
scripts/test_messages.sh
scripts/test_messages_streaming.sh
```

## Package

```bash
python -m pip install --upgrade build
python -m build
```

Artifacts are written to `dist/`.
