#!/usr/bin/env bash
set -euo pipefail
export CLAUDE_GATEWAY_API_KEY="${CLAUDE_GATEWAY_API_KEY:-dev-secret}"
python -m claude_gateway.server
