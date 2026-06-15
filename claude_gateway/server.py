from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
import uuid
from typing import Any, Awaitable, Callable

from aiohttp import web

from claude_gateway import __version__
from claude_gateway.config import GatewayConfig, load_config
from claude_gateway.protocols import (
    NormalizedRequest,
    ProtocolError,
    anthropic_error,
    build_prompt,
    json_dumps,
    normalize_chat_completion,
    normalize_messages,
    openai_error,
    usage_zero,
    with_history,
)
from claude_gateway.session_store import SessionStore
from claude_gateway.subprocess_runner import ClaudeCodeRunner, RunResult

logger = logging.getLogger(__name__)
KEEPALIVE_SECONDS = 30.0

_SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "0",
    "Referrer-Policy": "no-referrer",
}

_CORS_HEADERS = {
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Authorization, Content-Type, Idempotency-Key, X-Claude-Gateway-Session-Id, X-Hermes-Session-Id",
}


class ClaudeGatewayServer:
    def __init__(self, config: GatewayConfig):
        self.config = config
        self.sessions = SessionStore(config.sessions_dir)
        self.runner = ClaudeCodeRunner(
            claude_bin=config.claude_bin,
            claude_args=config.claude_args,
            workdir=config.workdir,
            timeout_seconds=config.request_timeout_seconds,
        )
        self.semaphore = asyncio.Semaphore(max(1, config.max_concurrent_runs))
        self._session_locks: dict[str, asyncio.Lock] = {}

    def create_app(self) -> web.Application:
        app = web.Application(
            middlewares=[self._cors_middleware, self._body_limit_middleware, self._security_headers_middleware],
            client_max_size=self.config.max_request_bytes,
        )
        app["gateway"] = self
        app.router.add_get("/health", self.handle_health)
        app.router.add_get("/v1/health", self.handle_health)
        app.router.add_get("/v1/models", self.handle_models)
        app.router.add_post("/v1/chat/completions", self.handle_chat_completions)
        app.router.add_post("/v1/messages", self.handle_messages)
        return app

    @web.middleware
    async def _security_headers_middleware(self, request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]) -> web.StreamResponse:
        response = await handler(request)
        for key, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        return response

    @web.middleware
    async def _body_limit_middleware(self, request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]) -> web.StreamResponse:
        if request.method in {"POST", "PUT", "PATCH"}:
            content_length = request.headers.get("Content-Length")
            if content_length is not None:
                try:
                    if int(content_length) > self.config.max_request_bytes:
                        return web.json_response(openai_error("Request body too large.", code="body_too_large"), status=413)
                except ValueError:
                    return web.json_response(openai_error("Invalid Content-Length header.", code="invalid_content_length"), status=400)
        return await handler(request)

    @web.middleware
    async def _cors_middleware(self, request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]) -> web.StreamResponse:
        origin = request.headers.get("Origin", "")
        cors_headers = self._cors_headers_for_origin(origin) if origin else None
        if origin and cors_headers is None:
            return web.Response(status=403)
        if request.method == "OPTIONS":
            return web.Response(status=200, headers=cors_headers or {})
        response = await handler(request)
        if cors_headers is not None:
            response.headers.update(cors_headers)
        return response

    def _cors_headers_for_origin(self, origin: str) -> dict[str, str] | None:
        if not origin or not self.config.cors_origins:
            return None
        if "*" in self.config.cors_origins:
            headers = dict(_CORS_HEADERS)
            headers["Access-Control-Allow-Origin"] = "*"
            headers["Access-Control-Max-Age"] = "600"
            return headers
        if origin not in self.config.cors_origins:
            return None
        headers = dict(_CORS_HEADERS)
        headers["Access-Control-Allow-Origin"] = origin
        headers["Vary"] = "Origin"
        headers["Access-Control-Max-Age"] = "600"
        return headers

    def _check_auth(self, request: web.Request, *, anthropic: bool = False) -> web.Response | None:
        if self.config.allow_no_auth:
            return None
        if not self.config.api_key:
            body = anthropic_error("API key is not configured", err_type="authentication_error") if anthropic else openai_error("API key is not configured", code="missing_api_key")
            return web.json_response(body, status=401)
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
            if hmac.compare_digest(token, self.config.api_key):
                return None
        body = anthropic_error("Invalid API key", err_type="authentication_error") if anthropic else openai_error("Invalid API key", code="invalid_api_key")
        return web.json_response(body, status=401)

    @staticmethod
    async def _read_json(request: web.Request, *, anthropic: bool = False) -> tuple[dict[str, Any] | None, web.Response | None]:
        try:
            body = await request.json()
        except Exception:
            error = anthropic_error("Invalid JSON in request body") if anthropic else openai_error("Invalid JSON in request body")
            return None, web.json_response(error, status=400)
        if not isinstance(body, dict):
            error = anthropic_error("Request body must be a JSON object") if anthropic else openai_error("Request body must be a JSON object")
            return None, web.json_response(error, status=400)
        return body, None

    def _session_id_for(self, request: web.Request, normalized: NormalizedRequest) -> str:
        provided = (
            request.headers.get("X-Claude-Gateway-Session-Id", "").strip()
            or request.headers.get("X-Hermes-Session-Id", "").strip()
            or str(normalized.raw.get("session_id") or "").strip()
        )
        if provided:
            return provided[:256]
        first_user = normalized.user_message
        for msg in normalized.history:
            if msg.get("role") == "user":
                first_user = msg.get("content", first_user)
                break
        return self.sessions.derive_session_id(normalized.system_prompt, first_user)

    def _session_lock(self, session_id: str) -> asyncio.Lock:
        lock = self._session_locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._session_locks[session_id] = lock
        return lock

    @staticmethod
    def _session_options(normalized: NormalizedRequest) -> dict[str, Any]:
        raw_session = normalized.raw.get("session")
        return raw_session if isinstance(raw_session, dict) else {}

    def _restore_session_history(self, session_id: str, normalized: NormalizedRequest) -> tuple[NormalizedRequest, dict[str, Any]]:
        options = self._session_options(normalized)
        mode = str(options.get("mode") or "resume").strip().lower()
        if mode == "reset" or options.get("reset") is True:
            self.sessions.delete(session_id)
            return normalized, {"session_resumed": False, "history_messages_used": len(normalized.history), "session_mode": "reset"}
        if mode == "stateless":
            return normalized, {"session_resumed": False, "history_messages_used": len(normalized.history), "session_mode": "stateless"}

        max_history = options.get("max_history_messages", 40)
        try:
            max_history_int = max(0, min(200, int(max_history)))
        except (TypeError, ValueError):
            max_history_int = 40
        stored_history = self.sessions.history(session_id, limit=max_history_int)
        if not stored_history:
            return normalized, {"session_resumed": False, "history_messages_used": len(normalized.history), "session_mode": "resume"}
        restored = with_history(normalized, stored_history + normalized.history)
        return restored, {
            "session_resumed": True,
            "history_messages_used": len(restored.history),
            "restored_history_messages": len(stored_history),
            "request_history_messages": len(normalized.history),
            "session_mode": "resume",
        }

    @staticmethod
    def _session_headers(session_id: str, request_id: str) -> dict[str, str]:
        return {
            "X-Claude-Gateway-Session-Id": session_id,
            "X-Hermes-Session-Id": session_id,
            "X-Request-Id": request_id,
        }

    async def _run_with_limit(self, prompt: str) -> RunResult | None:
        if self.semaphore.locked() and getattr(self.semaphore, "_value", 0) <= 0:
            return None
        await self.semaphore.acquire()
        try:
            return await self.runner.run(prompt)
        finally:
            self.semaphore.release()

    async def handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "platform": "claude-gateway", "version": __version__})

    async def handle_models(self, request: web.Request) -> web.Response:
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        return web.json_response({
            "object": "list",
            "data": [{
                "id": self.config.model_name,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "claude-gateway",
                "permission": [],
                "root": self.config.model_name,
                "parent": None,
            }],
        })

    async def handle_chat_completions(self, request: web.Request) -> web.StreamResponse:
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        body, err = await self._read_json(request)
        if err:
            return err
        assert body is not None
        try:
            normalized = normalize_chat_completion(body, self.config.model_name)
        except ProtocolError as exc:
            return web.json_response(openai_error(exc.message, param=exc.param, code=exc.code), status=400)
        session_id = self._session_id_for(request, normalized)
        request_id = f"req_{uuid.uuid4().hex}"
        async with self._session_lock(session_id):
            normalized, session_meta = self._restore_session_history(session_id, normalized)
            if normalized.stream:
                return await self._stream_chat_completion(request, normalized, session_id, request_id, session_meta)
            prompt = build_prompt(normalized, session_id=session_id)
            result = await self._run_with_limit(prompt)
            if result is None:
                return web.json_response(openai_error("Too many concurrent runs", code="rate_limit_exceeded"), status=429)
            self._record_session(session_id, normalized, result)
            status = 200 if result.completed or result.final_text else 502
            finish_reason = "stop" if result.completed else "error"
            response = {
                "id": f"chatcmpl-{uuid.uuid4().hex[:29]}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": normalized.model,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": result.final_text},
                    "finish_reason": finish_reason,
                }],
                "usage": self._openai_usage(result),
                "claude_gateway": self._gateway_metadata(session_id, result, session_meta),
            }
            if not result.completed:
                response["claude_gateway"].update({"error": result.error, "exit_code": result.exit_code})
            return web.json_response(response, status=status, headers=self._session_headers(session_id, request_id))

    async def handle_messages(self, request: web.Request) -> web.StreamResponse:
        auth_err = self._check_auth(request, anthropic=True)
        if auth_err:
            return auth_err
        body, err = await self._read_json(request, anthropic=True)
        if err:
            return err
        assert body is not None
        try:
            normalized = normalize_messages(body, self.config.model_name)
        except ProtocolError as exc:
            return web.json_response(anthropic_error(exc.message), status=400)
        session_id = self._session_id_for(request, normalized)
        request_id = f"req_{uuid.uuid4().hex}"
        async with self._session_lock(session_id):
            normalized, session_meta = self._restore_session_history(session_id, normalized)
            if normalized.stream:
                return await self._stream_messages(request, normalized, session_id, request_id, session_meta)
            prompt = build_prompt(normalized, session_id=session_id)
            result = await self._run_with_limit(prompt)
            if result is None:
                return web.json_response(anthropic_error("Too many concurrent runs", err_type="rate_limit_error"), status=429)
            self._record_session(session_id, normalized, result)
            status = 200 if result.completed or result.final_text else 529
            response = {
                "id": f"msg_{uuid.uuid4().hex[:24]}",
                "type": "message",
                "role": "assistant",
                "model": normalized.model,
                "content": [{"type": "text", "text": result.final_text}],
                "stop_reason": "end_turn" if result.completed else "error",
                "stop_sequence": None,
                "usage": {"input_tokens": int((result.usage or {}).get("input_tokens", 0) or 0), "output_tokens": int((result.usage or {}).get("output_tokens", 0) or 0)},
                "claude_gateway": self._gateway_metadata(session_id, result, session_meta),
            }
            if not result.completed:
                response["claude_gateway"].update({"error": result.error, "exit_code": result.exit_code})
            return web.json_response(response, status=status, headers=self._session_headers(session_id, request_id))

    async def _stream_chat_completion(self, request: web.Request, normalized: NormalizedRequest, session_id: str, request_id: str, session_meta: dict[str, Any] | None = None) -> web.StreamResponse:
        if self.semaphore.locked() and getattr(self.semaphore, "_value", 0) <= 0:
            return web.json_response(openai_error("Too many concurrent runs", code="rate_limit_exceeded"), status=429)
        await self.semaphore.acquire()
        completion_id = f"chatcmpl-{uuid.uuid4().hex[:29]}"
        created = int(time.time())
        headers = {"Content-Type": "text/event-stream", "Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        headers.update(self._session_headers(session_id, request_id))
        response = web.StreamResponse(status=200, headers=headers)
        await response.prepare(request)
        final_result: RunResult | None = None
        final_text_parts: list[str] = []
        try:
            role_chunk = {"id": completion_id, "object": "chat.completion.chunk", "created": created, "model": normalized.model, "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]}
            await response.write(f"data: {json_dumps(role_chunk)}\n\n".encode("utf-8"))
            prompt = build_prompt(normalized, session_id=session_id)
            async for item in self.runner.stream(prompt):
                if isinstance(item, RunResult):
                    final_result = item
                    break
                final_text_parts.append(item)
                chunk = {"id": completion_id, "object": "chat.completion.chunk", "created": created, "model": normalized.model, "choices": [{"index": 0, "delta": {"content": item}, "finish_reason": None}]}
                await response.write(f"data: {json_dumps(chunk)}\n\n".encode("utf-8"))
            if final_result is None:
                final_result = RunResult("".join(final_text_parts), 0, "", 0.0, True)
            self._record_session(session_id, normalized, final_result)
            finish = "stop" if final_result.completed else "error"
            finish_chunk = {"id": completion_id, "object": "chat.completion.chunk", "created": created, "model": normalized.model, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}], "usage": self._openai_usage(final_result), "claude_gateway": self._gateway_metadata(session_id, final_result, session_meta)}
            await response.write(f"data: {json_dumps(finish_chunk)}\n\n".encode("utf-8"))
            await response.write(b"data: [DONE]\n\n")
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, asyncio.CancelledError):
            raise
        finally:
            self.semaphore.release()
        return response

    async def _stream_messages(self, request: web.Request, normalized: NormalizedRequest, session_id: str, request_id: str, session_meta: dict[str, Any] | None = None) -> web.StreamResponse:
        if self.semaphore.locked() and getattr(self.semaphore, "_value", 0) <= 0:
            return web.json_response(anthropic_error("Too many concurrent runs", err_type="rate_limit_error"), status=429)
        await self.semaphore.acquire()
        message_id = f"msg_{uuid.uuid4().hex[:24]}"
        headers = {"Content-Type": "text/event-stream", "Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        headers.update(self._session_headers(session_id, request_id))
        response = web.StreamResponse(status=200, headers=headers)
        await response.prepare(request)
        final_result: RunResult | None = None
        final_text_parts: list[str] = []
        try:
            await self._write_event(response, "message_start", {"type": "message_start", "message": {"id": message_id, "type": "message", "role": "assistant", "model": normalized.model, "content": [], "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 0, "output_tokens": 0}, "metadata": {"session_id": session_id, **(session_meta or {})}}})
            await self._write_event(response, "content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
            prompt = build_prompt(normalized, session_id=session_id)
            async for item in self.runner.stream(prompt):
                if isinstance(item, RunResult):
                    final_result = item
                    break
                final_text_parts.append(item)
                await self._write_event(response, "content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": item}})
            if final_result is None:
                final_result = RunResult("".join(final_text_parts), 0, "", 0.0, True)
            self._record_session(session_id, normalized, final_result)
            await self._write_event(response, "content_block_stop", {"type": "content_block_stop", "index": 0})
            await self._write_event(response, "message_delta", {"type": "message_delta", "delta": {"stop_reason": final_result.stop_reason or ("end_turn" if final_result.completed else "error"), "stop_sequence": None}, "usage": {"output_tokens": int((final_result.usage or {}).get("output_tokens", 0) or 0)}, "claude_gateway": self._gateway_metadata(session_id, final_result, session_meta)})
            await self._write_event(response, "message_stop", {"type": "message_stop"})
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, asyncio.CancelledError):
            raise
        finally:
            self.semaphore.release()
        return response

    @staticmethod
    async def _write_event(response: web.StreamResponse, event: str, data: dict[str, Any]) -> None:
        await response.write(f"event: {event}\ndata: {json_dumps(data)}\n\n".encode("utf-8"))

    def _cache_usage(result: RunResult | None) -> dict[str, int]:
        usage = result.usage if result and isinstance(result.usage, dict) else {}
        cache_creation = usage.get("cache_creation") if isinstance(usage.get("cache_creation"), dict) else {}
        cache_creation_tokens = int(usage.get("cache_creation_input_tokens", 0) or 0)
        cache_read_tokens = int(usage.get("cache_read_input_tokens", 0) or 0)
        ephemeral_5m = int(cache_creation.get("ephemeral_5m_input_tokens", 0) or 0)
        ephemeral_1h = int(cache_creation.get("ephemeral_1h_input_tokens", 0) or 0)
        return {
            "cache_creation_input_tokens": cache_creation_tokens,
            "cache_read_input_tokens": cache_read_tokens,
            "cache_hit_input_tokens": cache_read_tokens,
            "cache_miss_input_tokens": cache_creation_tokens,
            "cache_creation_ephemeral_5m_input_tokens": ephemeral_5m,
            "cache_creation_ephemeral_1h_input_tokens": ephemeral_1h,
        }

    @classmethod
    def _gateway_metadata(cls, session_id: str, result: RunResult | None = None, session_meta: dict[str, Any] | None = None) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "session_id": session_id,
            "cache": cls._cache_usage(result),
        }
        if session_meta:
            metadata.update(session_meta)
        if result and result.session_id:
            metadata["claude_code_session_id"] = result.session_id
        if result and result.stop_reason:
            metadata["stop_reason"] = result.stop_reason
        return metadata

    @staticmethod
    def _openai_usage(result: RunResult | None = None) -> dict[str, int]:
        usage = (result.usage if result and isinstance(result.usage, dict) else None) or usage_zero()
        return {
            "prompt_tokens": int(usage.get("input_tokens", 0) or 0),
            "completion_tokens": int(usage.get("output_tokens", 0) or 0),
            "total_tokens": int(usage.get("total_tokens", 0) or ((usage.get("input_tokens", 0) or 0) + (usage.get("output_tokens", 0) or 0))),
        }

    def _record_session(self, session_id: str, normalized: NormalizedRequest, result: RunResult) -> None:
        try:
            self.sessions.upsert(
                session_id,
                append_messages=[
                    {"role": "user", "content": normalized.user_message, "ts": time.time()},
                    {"role": "assistant", "content": result.final_text, "ts": time.time()},
                ],
                metadata={"model": normalized.model, "completed": result.completed, "exit_code": result.exit_code},
            )
        except Exception:
            logger.debug("failed to persist session %s", session_id, exc_info=True)


def create_app(config: GatewayConfig | None = None) -> web.Application:
    return ClaudeGatewayServer(config or load_config()).create_app()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    config = load_config()
    if not config.can_start_without_auth:
        raise SystemExit("CLAUDE_GATEWAY_API_KEY is required unless CLAUDE_GATEWAY_ALLOW_NO_AUTH=1")
    app = create_app(config)
    web.run_app(app, host=config.host, port=config.port)


if __name__ == "__main__":
    main()
