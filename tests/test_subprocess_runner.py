from __future__ import annotations

import os
import sys
from pathlib import Path

from claude_gateway.subprocess_runner import ClaudeCodeRunner


async def test_subprocess_runner_success(tmp_path):
    runner = ClaudeCodeRunner(claude_bin=sys.executable, workdir=tmp_path, timeout_seconds=10)
    script = Path(__file__).parent / "fixtures" / "fake_claude.py"
    runner.build_command = lambda prompt: [sys.executable, str(script), "-p", prompt]
    result = await runner.run("hello")
    assert result.completed is True
    assert result.exit_code == 0
    assert "hello" in result.final_text


async def test_subprocess_runner_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_FAIL", "1")
    runner = ClaudeCodeRunner(claude_bin=sys.executable, workdir=tmp_path, timeout_seconds=10)
    script = Path(__file__).parent / "fixtures" / "fake_claude.py"
    runner.build_command = lambda prompt: [sys.executable, str(script), "-p", prompt]
    result = await runner.run("hello")
    assert result.completed is False
    assert result.exit_code == 7
    assert "fake claude failure" in result.stderr_tail


async def test_auth_required(client):
    resp = await client.get("/v1/models")
    assert resp.status == 401
