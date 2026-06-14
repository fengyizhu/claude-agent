from __future__ import annotations

from claude_gateway.protocols import normalize_messages


def test_normalize_messages_text_block():
    req = normalize_messages(
        {
            "model": "claude-code",
            "system": [{"type": "text", "text": "be direct"}],
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
        },
        "default-model",
    )
    assert req.model == "claude-code"
    assert req.system_prompt == "be direct"
    assert req.user_message == "hello"


async def test_messages_non_stream(client, auth_headers):
    resp = await client.post(
        "/v1/messages",
        headers=auth_headers,
        json={"model": "claude-code", "max_tokens": 100, "messages": [{"role": "user", "content": "hello"}]},
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["type"] == "message"
    assert data["role"] == "assistant"
    assert data["content"][0]["type"] == "text"
    assert "hello" in data["content"][0]["text"]
    assert data["claude_gateway"]["cache"]["cache_read_input_tokens"] == 4
    assert data["claude_gateway"]["cache"]["cache_creation_input_tokens"] == 1


async def test_messages_unsupported_content(client, auth_headers):
    resp = await client.post(
        "/v1/messages",
        headers=auth_headers,
        json={"messages": [{"role": "user", "content": [{"type": "image", "source": {}}]}]},
    )
    assert resp.status == 400
    data = await resp.json()
    assert data["type"] == "error"
