#!/usr/bin/env bash
set -euo pipefail
: "${CLAUDE_GATEWAY_API_KEY:=dev-secret}"
curl -sS http://127.0.0.1:8765/v1/chat/completions \
  -H "Authorization: Bearer ${CLAUDE_GATEWAY_API_KEY}" \
  -H 'Content-Type: application/json' \
  -d '{"model":"claude-code","messages":[{"role":"user","content":"Say hello from the gateway"}],"stream":false}'
