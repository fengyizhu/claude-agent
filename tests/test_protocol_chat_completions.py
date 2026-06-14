from __future__ import annotations

from claude_gateway.protocols import normalize_chat_completion


def test_normalize_chat_completion_folds_system():
    req = normalize_chat_completion(
        {
            "model": "claude-code",
            "messages": [
                {"role": "system", "content": "be concise"},
                {"role": "user", "content": "hello"},
            ],
        },
        "default-model",
    )
    assert req.model == "claude-code"
    assert req.system_prompt == "be concise"
    assert req.user_message == "hello"


async def test_chat_completions_non_stream(client, auth_headers):
    resp = await client.post(
        "/v1/chat/completions",
        headers=auth_headers,
        json={"model": "claude-code", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["object"] == "chat.completion"
    assert data["choices"][0]["message"]["role"] == "assistant"
    assert "hello" in data["choices"][0]["message"]["content"]
    assert data["claude_gateway"]["cache"]["cache_read_input_tokens"] == 4
    assert data["claude_gateway"]["cache"]["cache_creation_input_tokens"] == 1
    assert resp.headers["X-Claude-Gateway-Session-Id"]


async def test_chat_completions_missing_messages(client, auth_headers):
    resp = await client.post("/v1/chat/completions", headers=auth_headers, json={})
    assert resp.status == 400
    data = await resp.json()
    assert data["error"]["code"] == "missing_messages"
