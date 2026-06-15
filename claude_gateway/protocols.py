from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any

_TRUE_REQUEST_BOOL_STRINGS = frozenset({"1", "true", "yes", "on"})
_FALSE_REQUEST_BOOL_STRINGS = frozenset({"0", "false", "no", "off"})
MAX_NORMALIZED_TEXT_LENGTH = 65_536
MAX_CONTENT_LIST_SIZE = 1_000


class ProtocolError(ValueError):
    def __init__(self, message: str, *, code: str = "invalid_request", param: str | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.param = param


@dataclass(frozen=True)
class NormalizedRequest:
    model: str
    system_prompt: str | None
    history: list[dict[str, str]]
    user_message: str
    stream: bool
    raw: dict[str, Any]


def coerce_request_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _TRUE_REQUEST_BOOL_STRINGS:
            return True
        if normalized in _FALSE_REQUEST_BOOL_STRINGS:
            return False
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def normalize_text_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content[:MAX_NORMALIZED_TEXT_LENGTH]
    if isinstance(content, list):
        parts: list[str] = []
        for item in content[:MAX_CONTENT_LIST_SIZE]:
            if isinstance(item, str):
                parts.append(item[:MAX_NORMALIZED_TEXT_LENGTH])
            elif isinstance(item, dict):
                item_type = str(item.get("type") or "").strip().lower()
                if item_type in {"text", "input_text", "output_text"}:
                    text = item.get("text", "")
                    if text is not None:
                        parts.append(str(text)[:MAX_NORMALIZED_TEXT_LENGTH])
                elif item_type in {"image", "image_url", "input_image", "tool_use", "tool_result", "file", "input_file"}:
                    raise ProtocolError(
                        f"Unsupported content part type {item_type!r}; only text content is supported in this gateway version.",
                        code="unsupported_content_type",
                    )
                elif item_type:
                    raise ProtocolError(
                        f"Unsupported content part type {item_type!r}; only text content is supported in this gateway version.",
                        code="unsupported_content_type",
                    )
            elif isinstance(item, list):
                nested = normalize_text_content(item)
                if nested:
                    parts.append(nested)
            if sum(len(part) for part in parts) >= MAX_NORMALIZED_TEXT_LENGTH:
                break
        return "\n".join(parts)[:MAX_NORMALIZED_TEXT_LENGTH]
    try:
        return str(content)[:MAX_NORMALIZED_TEXT_LENGTH]
    except Exception as exc:
        raise ProtocolError("Content could not be converted to text", code="invalid_content") from exc


def _visible(content: str) -> bool:
    return bool(content.strip())


def normalize_chat_completion(body: dict[str, Any], default_model: str) -> NormalizedRequest:
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ProtocolError("Missing or invalid 'messages' field", code="missing_messages", param="messages")

    system_parts: list[str] = []
    conversation: list[dict[str, str]] = []
    for idx, msg in enumerate(messages):
        if not isinstance(msg, dict):
            raise ProtocolError(f"messages[{idx}] must be an object", code="invalid_message", param=f"messages[{idx}]")
        role = str(msg.get("role") or "").strip().lower()
        if role == "system":
            system_parts.append(normalize_text_content(msg.get("content", "")))
        elif role in {"user", "assistant"}:
            content = normalize_text_content(msg.get("content", ""))
            if content:
                conversation.append({"role": role, "content": content})
        elif role in {"tool", "function"}:
            continue
        else:
            raise ProtocolError(f"Unsupported message role {role!r}", code="unsupported_role", param=f"messages[{idx}].role")

    last_user_index = None
    for idx in range(len(conversation) - 1, -1, -1):
        if conversation[idx]["role"] == "user":
            last_user_index = idx
            break
    if last_user_index is None or not _visible(conversation[last_user_index]["content"]):
        raise ProtocolError("No user message found in messages", code="missing_user_message", param="messages")

    user_message = conversation[last_user_index]["content"]
    history = conversation[:last_user_index]
    return NormalizedRequest(
        model=str(body.get("model") or default_model),
        system_prompt="\n".join(part for part in system_parts if part) or None,
        history=history,
        user_message=user_message,
        stream=coerce_request_bool(body.get("stream"), default=False),
        raw=body,
    )


def _normalize_anthropic_system(system: Any) -> str | None:
    if system is None:
        return None
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        return normalize_text_content(system)
    raise ProtocolError("system must be a string or text content block array", code="invalid_system", param="system")


def normalize_messages(body: dict[str, Any], default_model: str) -> NormalizedRequest:
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ProtocolError("Missing or invalid 'messages' field", code="missing_messages", param="messages")

    conversation: list[dict[str, str]] = []
    for idx, msg in enumerate(messages):
        if not isinstance(msg, dict):
            raise ProtocolError(f"messages[{idx}] must be an object", code="invalid_message", param=f"messages[{idx}]")
        role = str(msg.get("role") or "").strip().lower()
        if role not in {"user", "assistant"}:
            raise ProtocolError(f"Unsupported message role {role!r}", code="unsupported_role", param=f"messages[{idx}].role")
        content = normalize_text_content(msg.get("content", ""))
        if content:
            conversation.append({"role": role, "content": content})

    last_user_index = None
    for idx in range(len(conversation) - 1, -1, -1):
        if conversation[idx]["role"] == "user":
            last_user_index = idx
            break
    if last_user_index is None or not _visible(conversation[last_user_index]["content"]):
        raise ProtocolError("No user message found in messages", code="missing_user_message", param="messages")

    return NormalizedRequest(
        model=str(body.get("model") or default_model),
        system_prompt=_normalize_anthropic_system(body.get("system")),
        history=conversation[:last_user_index],
        user_message=conversation[last_user_index]["content"],
        stream=coerce_request_bool(body.get("stream"), default=False),
        raw=body,
    )


def with_history(request: NormalizedRequest, history: list[dict[str, str]]) -> NormalizedRequest:
    return replace(request, history=history)


def build_prompt(request: NormalizedRequest, *, session_id: str | None = None) -> str:
    sections: list[str] = []
    if request.system_prompt:
        sections.append(f"System instructions:\n{request.system_prompt}")
    if session_id:
        sections.append(f"Session: {session_id}")
    if request.history:
        transcript = []
        for msg in request.history:
            role = msg["role"].upper()
            transcript.append(f"{role}: {msg['content']}")
        sections.append("Conversation so far:\n" + "\n\n".join(transcript))
    sections.append("User request:\n" + request.user_message)
    return "\n\n".join(sections)


def openai_error(message: str, *, err_type: str = "invalid_request_error", param: str | None = None, code: str | None = None) -> dict[str, Any]:
    return {"error": {"message": message, "type": err_type, "param": param, "code": code}}


def anthropic_error(message: str, *, err_type: str = "invalid_request_error") -> dict[str, Any]:
    return {"type": "error", "error": {"type": err_type, "message": message}}


def usage_zero() -> dict[str, int]:
    return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
