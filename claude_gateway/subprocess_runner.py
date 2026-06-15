from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunResult:
    final_text: str
    exit_code: int | None
    stderr_tail: str
    duration_seconds: float
    completed: bool
    usage: dict[str, Any] | None = None
    session_id: str | None = None
    stop_reason: str | None = None
    cancelled: bool = False
    error: str | None = None


class ClaudeCodeRunner:
    def __init__(
        self,
        *,
        claude_bin: str = "claude",
        claude_args: tuple[str, ...] | list[str] | None = None,
        workdir: Path | str | None = None,
        timeout_seconds: float = 1800.0,
    ) -> None:
        self.claude_bin = claude_bin
        self.claude_args = tuple(claude_args or ())
        self.workdir = Path(workdir or Path.cwd())
        self.timeout_seconds = timeout_seconds

    def build_command(self, prompt: str) -> list[str]:
        # Claude Code supports non-interactive print mode via `claude -p <prompt>`.
        # Stream JSON exposes structured message_start/content_block_delta/result
        # events that are better suited for an HTTP gateway than rendered text.
        return [
            self.claude_bin,
            *self.claude_args,
            "-p",
            "--verbose",
            "--output-format",
            "stream-json",
            "--include-partial-messages",
            prompt,
        ]

    @staticmethod
    def _extract_text_from_event(obj: dict[str, Any]) -> str:
        if obj.get("type") == "stream_event":
            event = obj.get("event") or {}
            if event.get("type") == "content_block_delta":
                delta = event.get("delta") or {}
                if delta.get("type") == "text_delta":
                    return str(delta.get("text") or "")
            return ""
        if obj.get("type") == "assistant":
            message = obj.get("message") or {}
            parts: list[str] = []
            for block in message.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text") or ""))
            return "".join(parts)
        return ""

    @staticmethod
    def _result_from_json_event(obj: dict[str, Any], *, started: float, fallback_text: str, exit_code: int | None = 0) -> RunResult | None:
        if obj.get("type") != "result":
            return None
        is_error = bool(obj.get("is_error")) or obj.get("subtype") not in {"success", None}
        result_text = str(obj.get("result") or fallback_text or "")
        usage = obj.get("usage") if isinstance(obj.get("usage"), dict) else None
        error = obj.get("error") or obj.get("api_error_status")
        return RunResult(
            final_text=result_text,
            exit_code=exit_code,
            stderr_tail="",
            duration_seconds=time.monotonic() - started,
            completed=not is_error,
            usage=usage,
            session_id=str(obj.get("session_id") or "") or None,
            stop_reason=str(obj.get("stop_reason") or "") or None,
            error=str(error) if error else None,
        )

    async def run(self, prompt: str) -> RunResult:
        chunks: list[str] = []
        stderr_chunks: list[str] = []
        started = time.monotonic()
        process = await self._start(prompt)
        try:
            await asyncio.wait_for(
                self._drain_process(process, chunks=chunks, stderr_chunks=stderr_chunks),
                timeout=self.timeout_seconds,
            )
            exit_code = await process.wait()
            stderr_tail = self._tail("".join(stderr_chunks))
            parsed_text, parsed_result = self._parse_json_output("".join(chunks), started=started, exit_code=exit_code)
            final_text = parsed_result.final_text if parsed_result is not None else parsed_text
            if parsed_result is not None:
                if stderr_tail and not parsed_result.stderr_tail:
                    return RunResult(
                        final_text=parsed_result.final_text,
                        exit_code=exit_code,
                        stderr_tail=stderr_tail,
                        duration_seconds=parsed_result.duration_seconds,
                        completed=parsed_result.completed and exit_code == 0,
                        usage=parsed_result.usage,
                        session_id=parsed_result.session_id,
                        stop_reason=parsed_result.stop_reason,
                        error=parsed_result.error,
                    )
                return parsed_result
            if exit_code == 0:
                return RunResult(
                    final_text=final_text,
                    exit_code=exit_code,
                    stderr_tail=stderr_tail,
                    duration_seconds=time.monotonic() - started,
                    completed=True,
                )
            return RunResult(
                final_text=final_text,
                exit_code=exit_code,
                stderr_tail=stderr_tail,
                duration_seconds=time.monotonic() - started,
                completed=False,
                error=stderr_tail or f"Claude Code exited with status {exit_code}",
            )
        except asyncio.TimeoutError:
            await self._terminate(process)
            return RunResult(
                final_text="".join(chunks),
                exit_code=process.returncode,
                stderr_tail=self._tail("".join(stderr_chunks)),
                duration_seconds=time.monotonic() - started,
                completed=False,
                error="Claude Code subprocess timed out",
            )
        except asyncio.CancelledError:
            await self._terminate(process)
            raise

    async def stream(self, prompt: str) -> AsyncIterator[str | RunResult]:
        chunks: list[str] = []
        final_text_parts: list[str] = []
        stderr_chunks: list[str] = []
        started = time.monotonic()
        process = await self._start(prompt)
        stderr_task = asyncio.create_task(self._read_stderr(process, stderr_chunks))
        try:
            assert process.stdout is not None
            final_result: RunResult | None = None
            while True:
                try:
                    line = await asyncio.wait_for(process.stdout.readline(), timeout=self.timeout_seconds)
                except asyncio.TimeoutError:
                    await self._terminate(process)
                    yield RunResult(
                        final_text="".join(final_text_parts),
                        exit_code=process.returncode,
                        stderr_tail=self._tail("".join(stderr_chunks)),
                        duration_seconds=time.monotonic() - started,
                        completed=False,
                        error="Claude Code subprocess timed out",
                    )
                    return
                if not line:
                    break
                text = line.decode("utf-8", errors="replace")
                chunks.append(text)
                try:
                    obj = json.loads(text)
                except json.JSONDecodeError:
                    final_text_parts.append(text)
                    yield text
                    continue

                delta = self._extract_text_from_event(obj)
                if delta and obj.get("type") == "stream_event":
                    final_text_parts.append(delta)
                    yield delta
                    continue

                parsed_result = self._result_from_json_event(
                    obj,
                    started=started,
                    fallback_text="".join(final_text_parts),
                    exit_code=process.returncode,
                )
                if parsed_result is not None:
                    final_result = parsed_result

            exit_code = await process.wait()
            await stderr_task
            stderr_tail = self._tail("".join(stderr_chunks))
            if final_result is None:
                parsed_text, parsed_result = self._parse_json_output("".join(chunks), started=started, exit_code=exit_code)
                final_result = parsed_result or RunResult(
                    final_text=parsed_text,
                    exit_code=exit_code,
                    stderr_tail=stderr_tail,
                    duration_seconds=time.monotonic() - started,
                    completed=exit_code == 0,
                    error=None if exit_code == 0 else (stderr_tail or f"Claude Code exited with status {exit_code}"),
                )
            elif stderr_tail and not final_result.stderr_tail:
                final_result = RunResult(
                    final_text=final_result.final_text,
                    exit_code=exit_code,
                    stderr_tail=stderr_tail,
                    duration_seconds=final_result.duration_seconds,
                    completed=final_result.completed and exit_code == 0,
                    usage=final_result.usage,
                    session_id=final_result.session_id,
                    stop_reason=final_result.stop_reason,
                    error=final_result.error,
                )
            yield final_result
        except asyncio.CancelledError:
            await self._terminate(process)
            stderr_task.cancel()
            raise
        finally:
            if not stderr_task.done():
                stderr_task.cancel()

    async def _start(self, prompt: str) -> asyncio.subprocess.Process:
        self.workdir.mkdir(parents=True, exist_ok=True)
        command = self.build_command(prompt)
        logger.debug(
            "starting Claude Code subprocess cwd=%s command=%s prompt_chars=%s",
            self.workdir,
            command[:-1] + ["<prompt>"],
            len(prompt),
        )
        return await asyncio.create_subprocess_exec(
            *command,
            cwd=str(self.workdir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def _drain_process(
        self,
        process: asyncio.subprocess.Process,
        *,
        chunks: list[str],
        stderr_chunks: list[str],
    ) -> None:
        await asyncio.gather(
            self._read_stdout(process, chunks),
            self._read_stderr(process, stderr_chunks),
        )

    @staticmethod
    async def _read_stdout(process: asyncio.subprocess.Process, chunks: list[str]) -> None:
        assert process.stdout is not None
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            chunks.append(line.decode("utf-8", errors="replace"))

    @staticmethod
    async def _read_stderr(process: asyncio.subprocess.Process, stderr_chunks: list[str]) -> None:
        assert process.stderr is not None
        while True:
            line = await process.stderr.readline()
            if not line:
                break
            stderr_chunks.append(line.decode("utf-8", errors="replace"))

    @staticmethod
    async def _terminate(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()

    @staticmethod
    def _tail(text: str, limit: int = 4000) -> str:
        cleaned = text.replace("\x00", "").strip()
        if len(cleaned) <= limit:
            return cleaned
        return cleaned[-limit:]

    @classmethod
    def _parse_json_output(cls, text: str, *, started: float, exit_code: int | None) -> tuple[str, RunResult | None]:
        parts: list[str] = []
        result: RunResult | None = None
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                parts.append(line + "\n")
                continue
            delta = cls._extract_text_from_event(obj)
            if delta and obj.get("type") == "stream_event":
                parts.append(delta)
            parsed_result = cls._result_from_json_event(
                obj,
                started=started,
                fallback_text="".join(parts),
                exit_code=exit_code,
            )
            if parsed_result is not None:
                result = parsed_result
        return "".join(parts), result
