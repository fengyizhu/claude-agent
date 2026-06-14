#!/usr/bin/env bash
set -euo pipefail
: "${CLAUDE_GATEWAY_API_KEY:=dev-secret}"
curl -N http://127.0.0.1:8765/v1/messages \
  -H "Authorization: Bearer ${CLAUDE_GATEWAY_API_KEY}" \
  -H 'Content-Type: application/json' \
  -d '{"model":"claude-code","max_tokens":1024,"messages":[{"role":"user","content":"Stream via Anthropic messages SSE"}],"stream":true}'
