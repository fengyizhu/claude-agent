from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_gateway.config import GatewayConfig
from claude_gateway.server import create_app


@pytest.fixture
def fake_claude_bin() -> str:
    return sys.executable


@pytest.fixture
def fake_claude_script() -> str:
    return str(Path(__file__).parent / "fixtures" / "fake_claude.py")


@pytest.fixture
async def client(tmp_path, fake_claude_script) -> TestClient:
    config = GatewayConfig(
        api_key="test-secret",
        claude_bin=sys.executable,
        workdir=tmp_path,
        sessions_dir=tmp_path / ".sessions",
        request_timeout_seconds=10,
    )
    app = create_app(config)
    gateway = app["gateway"]
    gateway.runner.build_command = lambda prompt: [
        sys.executable,
        fake_claude_script,
        "-p",
        "--output-format",
        "stream-json",
        "--include-partial-messages",
        prompt,
    ]
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        yield test_client
    finally:
        await test_client.close()


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-secret", "Content-Type": "application/json"}
