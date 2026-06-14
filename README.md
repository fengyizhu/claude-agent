# Claude Gateway

Claude Gateway exposes local HTTP endpoints compatible with OpenAI Chat Completions and Anthropic Messages, backed by Claude Code subprocess execution.

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
CLAUDE_GATEWAY_API_KEY=dev-secret python -m claude_gateway.server
```

## Endpoints

- `GET /health`
- `GET /v1/health`
- `GET /v1/models`
- `POST /v1/chat/completions`
- `POST /v1/messages`
