#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import time


def main() -> int:
    prompt = " ".join(sys.argv[2:]) if len(sys.argv) > 2 and sys.argv[1] == "-p" else " ".join(sys.argv[1:])
    if os.environ.get("FAKE_CLAUDE_FAIL") == "1":
        print("fake claude failure", file=sys.stderr)
        return 7
    delay = float(os.environ.get("FAKE_CLAUDE_DELAY", "0"))
    if os.environ.get("FAKE_CLAUDE_STREAM") == "1":
        if "stream-json" in sys.argv:
            events = [
                {"type": "stream_event", "event": {"type": "message_start", "message": {"usage": {"input_tokens": 1, "output_tokens": 0}}}},
                {"type": "stream_event", "event": {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}},
                {"type": "stream_event", "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "alpha "}}},
                {"type": "stream_event", "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "beta "}}},
                {"type": "stream_event", "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "gamma\n"}}},
                {"type": "result", "subtype": "success", "is_error": False, "result": "alpha beta gamma\n", "stop_reason": "end_turn", "session_id": "fake-session", "usage": {"input_tokens": 10, "output_tokens": 3, "cache_read_input_tokens": 7, "cache_creation_input_tokens": 2, "cache_creation": {"ephemeral_5m_input_tokens": 2, "ephemeral_1h_input_tokens": 0}}},
            ]
            for event in events:
                sys.stdout.write(json.dumps(event) + "\n")
                sys.stdout.flush()
                if delay:
                    time.sleep(delay)
            return 0
        for part in ("alpha ", "beta ", "gamma\n"):
            sys.stdout.write(part)
            sys.stdout.flush()
            if delay:
                time.sleep(delay)
        return 0
    if "stream-json" in sys.argv:
        text = "fake response: " + prompt[:200] + "\n"
        events = [
            {"type": "stream_event", "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}}},
            {"type": "result", "subtype": "success", "is_error": False, "result": text, "stop_reason": "end_turn", "session_id": "fake-session", "usage": {"input_tokens": 8, "output_tokens": 1, "cache_read_input_tokens": 4, "cache_creation_input_tokens": 1, "cache_creation": {"ephemeral_5m_input_tokens": 1, "ephemeral_1h_input_tokens": 0}}},
        ]
        for event in events:
            sys.stdout.write(json.dumps(event) + "\n")
        return 0
    sys.stdout.write("fake response: " + prompt[:200] + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
