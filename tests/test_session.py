from __future__ import annotations

from claude_gateway.protocols import NormalizedRequest
from claude_gateway.server import ClaudeGatewayServer


def _request(message: str, *, history=None, raw=None) -> NormalizedRequest:
    return NormalizedRequest(
        model="claude-code",
        system_prompt=None,
        history=list(history or []),
        user_message=message,
        stream=False,
        raw=dict(raw or {}),
    )


def test_restore_session_history_uses_store(tmp_path):
    server = ClaudeGatewayServer.__new__(ClaudeGatewayServer)
    from claude_gateway.session_store import SessionStore

    server.sessions = SessionStore(tmp_path)
    server.sessions.upsert(
        "demo",
        append_messages=[
            {"role": "user", "content": "remember blue-river"},
            {"role": "assistant", "content": "remembered"},
        ],
    )

    restored, meta = server._restore_session_history("demo", _request("what is my code?"))
    assert meta["session_resumed"] is True
    assert meta["restored_history_messages"] == 2
    assert restored.history == [
        {"role": "user", "content": "remember blue-river"},
        {"role": "assistant", "content": "remembered"},
    ]


def test_restore_session_history_stateless_skips_store(tmp_path):
    server = ClaudeGatewayServer.__new__(ClaudeGatewayServer)
    from claude_gateway.session_store import SessionStore

    server.sessions = SessionStore(tmp_path)
    server.sessions.upsert("demo", append_messages=[{"role": "user", "content": "stored"}])
    restored, meta = server._restore_session_history("demo", _request("now", raw={"session": {"mode": "stateless"}}))
    assert meta["session_resumed"] is False
    assert meta["session_mode"] == "stateless"
    assert restored.history == []


def test_restore_session_history_reset_clears_store(tmp_path):
    server = ClaudeGatewayServer.__new__(ClaudeGatewayServer)
    from claude_gateway.session_store import SessionStore

    server.sessions = SessionStore(tmp_path)
    server.sessions.upsert("demo", append_messages=[{"role": "user", "content": "stored"}])
    restored, meta = server._restore_session_history("demo", _request("now", raw={"session": {"mode": "reset"}}))
    assert meta["session_mode"] == "reset"
    assert restored.history == []
    assert server.sessions.history("demo") == []
