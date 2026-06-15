from __future__ import annotations

import os


async def test_chat_completions_sse(client, auth_headers, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_STREAM", "1")
    resp = await client.post(
        "/v1/chat/completions",
        headers=auth_headers,
        json={"messages": [{"role": "user", "content": "stream"}], "stream": True},
    )
    assert resp.status == 200
    text = await resp.text()
    assert '"delta":{"role":"assistant"}' in text
    assert '"content":"alpha ' in text
    assert '"claude_gateway"' not in text
    assert "data: [DONE]" in text


async def test_messages_sse(client, auth_headers, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_STREAM", "1")
    resp = await client.post(
        "/v1/messages",
        headers=auth_headers,
        json={"messages": [{"role": "user", "content": "stream"}], "stream": True},
    )
    assert resp.status == 200
    text = await resp.text()
    assert "event: message_start" in text
    assert "event: content_block_start" in text
    assert "event: content_block_delta" in text
    assert "event: content_block_stop" in text
    assert "event: message_delta" in text
    assert '"cache_read_input_tokens":7' in text
    assert "event: message_stop" in text
