from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_MODEL_NAME = "claude-code"
DEFAULT_REQUEST_TIMEOUT_SECONDS = 1800.0
DEFAULT_MAX_CONCURRENT_RUNS = 4
DEFAULT_MAX_REQUEST_BYTES = 10_000_000

_TRUE_STRINGS = frozenset({"1", "true", "yes", "on"})
_FALSE_STRINGS = frozenset({"0", "false", "no", "off"})


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in _TRUE_STRINGS:
        return True
    if normalized in _FALSE_STRINGS:
        return False
    return default


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _csv_env(name: str) -> tuple[str, ...]:
    raw = os.getenv(name, "")
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _argv_env(name: str) -> tuple[str, ...]:
    raw = os.getenv(name, "")
    if not raw.strip():
        return ()
    return tuple(shlex.split(raw))


@dataclass(frozen=True)
class GatewayConfig:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    api_key: str = ""
    allow_no_auth: bool = False
    debug: bool = False
    cors_origins: tuple[str, ...] = field(default_factory=tuple)
    model_name: str = DEFAULT_MODEL_NAME
    claude_bin: str = "claude"
    claude_args: tuple[str, ...] = field(default_factory=tuple)
    workdir: Path = field(default_factory=Path.cwd)
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS
    max_concurrent_runs: int = DEFAULT_MAX_CONCURRENT_RUNS
    max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES
    sessions_dir: Path = field(default_factory=lambda: Path.cwd() / ".sessions")

    @property
    def auth_required(self) -> bool:
        return bool(self.api_key) and not self.allow_no_auth

    @property
    def can_start_without_auth(self) -> bool:
        return bool(self.api_key) or self.allow_no_auth


def load_config(overrides: dict[str, Any] | None = None) -> GatewayConfig:
    overrides = overrides or {}
    workdir = Path(overrides.get("workdir") or os.getenv("CLAUDE_GATEWAY_WORKDIR") or Path.cwd()).expanduser()
    sessions_dir = Path(
        overrides.get("sessions_dir")
        or os.getenv("CLAUDE_GATEWAY_SESSIONS_DIR")
        or (workdir / ".sessions")
    ).expanduser()
    return GatewayConfig(
        host=str(overrides.get("host") or os.getenv("CLAUDE_GATEWAY_HOST", DEFAULT_HOST)),
        port=int(overrides.get("port") or _int_env("CLAUDE_GATEWAY_PORT", DEFAULT_PORT)),
        api_key=str(overrides.get("api_key") or os.getenv("CLAUDE_GATEWAY_API_KEY", "")),
        allow_no_auth=bool(overrides.get("allow_no_auth", _bool_env("CLAUDE_GATEWAY_ALLOW_NO_AUTH", False))),
        debug=bool(overrides.get("debug", _bool_env("CLAUDE_GATEWAY_DEBUG", False))),
        cors_origins=tuple(overrides.get("cors_origins") or _csv_env("CLAUDE_GATEWAY_CORS_ORIGINS")),
        model_name=str(overrides.get("model_name") or os.getenv("CLAUDE_GATEWAY_MODEL_NAME", DEFAULT_MODEL_NAME)),
        claude_bin=str(overrides.get("claude_bin") or os.getenv("CLAUDE_GATEWAY_CLAUDE_BIN", "claude")),
        claude_args=tuple(overrides.get("claude_args") or _argv_env("CLAUDE_GATEWAY_CLAUDE_ARGS")),
        workdir=workdir,
        request_timeout_seconds=float(
            overrides.get("request_timeout_seconds")
            or _float_env("CLAUDE_GATEWAY_REQUEST_TIMEOUT_SECONDS", DEFAULT_REQUEST_TIMEOUT_SECONDS)
        ),
        max_concurrent_runs=int(
            overrides.get("max_concurrent_runs")
            or _int_env("CLAUDE_GATEWAY_MAX_CONCURRENT_RUNS", DEFAULT_MAX_CONCURRENT_RUNS)
        ),
        max_request_bytes=int(
            overrides.get("max_request_bytes")
            or _int_env("CLAUDE_GATEWAY_MAX_REQUEST_BYTES", DEFAULT_MAX_REQUEST_BYTES)
        ),
        sessions_dir=sessions_dir,
    )
