from __future__ import annotations

from claude_gateway.config import load_config
from claude_gateway.subprocess_runner import ClaudeCodeRunner


def test_load_config_parses_claude_args(monkeypatch):
    monkeypatch.setenv("CLAUDE_GATEWAY_CLAUDE_ARGS", "--dangerously-skip-permissions --model opus")
    config = load_config({"allow_no_auth": True})
    assert config.claude_args == ("--dangerously-skip-permissions", "--model", "opus")


def test_runner_build_command_includes_extra_args(tmp_path):
    runner = ClaudeCodeRunner(
        claude_bin="claude",
        claude_args=("--dangerously-skip-permissions",),
        workdir=tmp_path,
    )
    assert runner.build_command("hello") == [
        "claude",
        "--dangerously-skip-permissions",
        "-p",
        "--verbose",
        "--output-format",
        "stream-json",
        "--include-partial-messages",
        "hello",
    ]
