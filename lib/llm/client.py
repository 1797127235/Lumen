"""LLM 客户端 — OpenAI 兼容 Chat Completions API 直接调用。"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from lib.metrics import record
from shared.logging import get_logger

logger = get_logger(__name__)


class ContextLengthError(Exception):
    """LLM 上下文超长。"""


_CONTEXT_LENGTH_KEYWORDS = (
    "maximum context length",
    "context_length_exceeded",
    "range of input length",
    "context window exceeds limit",
    "string too long",
    "reduce the length",
    "too many tokens",
)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    thinking: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    provider_fields: dict[str, Any] = field(default_factory=dict)


class LLMClient:
    """直接调用 OpenAI 兼容 Chat Completions API。"""

    def __init__(self, api_key: str, base_url: str, model: str):
        self.client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=120.0,
        )
        self.model = model

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 8192,
        stream: bool = False,
        on_content_delta: Any = None,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        if stream and on_content_delta:
            collected = ""
            async for chunk in self._chat_stream(payload):
                collected += chunk
                await on_content_delta({"content_delta": chunk})
            return LLMResponse(content=collected or None)

        started = time.perf_counter()
        status = "ok"
        try:
            resp = await self.client.post("/chat/completions", json=payload)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            error_text = exc.response.text
            lower = error_text.lower()
            if any(kw in lower for kw in _CONTEXT_LENGTH_KEYWORDS):
                status = "context_overflow"
                await record("llm.context_overflow", 1.0, labels={"model": self.model})
                raise ContextLengthError(error_text) from exc
            status = "error"
            raise

        data = resp.json()

        choice = data["choices"][0]
        message = choice["message"]

        raw_usage = data.get("usage") or {}

        tool_calls: list[ToolCall] = []
        for tc in message.get("tool_calls", []):
            tool_calls.append(
                ToolCall(
                    id=tc["id"],
                    name=tc["function"]["name"],
                    arguments=json.loads(tc["function"]["arguments"]),
                )
            )

        usage = {
            "input": raw_usage.get("prompt_tokens", 0),
            "output": raw_usage.get("completion_tokens", 0),
            "cache_read": raw_usage.get("prompt_cache_hit_tokens", 0),
            "cache_write": raw_usage.get("prompt_cache_miss_tokens", 0),
        }

        # ── metrics 埋点：LLM 调用 token / 耗时 ──
        # fire-and-forget，绝不阻塞 LLM 流程
        latency_ms = (time.perf_counter() - started) * 1000
        model_label = {"model": self.model}
        await record("llm.call_duration_ms", latency_ms, labels={**model_label, "status": status})
        await record("llm.calls", 1.0, labels={**model_label, "status": status})
        if usage["input"]:
            await record("llm.tokens.input", float(usage["input"]), labels=model_label)
        if usage["output"]:
            await record("llm.tokens.output", float(usage["output"]), labels=model_label)
        if usage["cache_read"]:
            await record("llm.tokens.cache_read", float(usage["cache_read"]), labels=model_label)
        if usage["cache_write"]:
            await record("llm.tokens.cache_write", float(usage["cache_write"]), labels=model_label)

        return LLMResponse(
            content=message.get("content"),
            tool_calls=tool_calls,
            usage=usage,
        )

    async def _chat_stream(self, payload: dict[str, Any]) -> AsyncIterator[str]:
        """流式返回 token。"""
        async with self.client.stream("POST", "/chat/completions", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                        delta = chunk["choices"][0]["delta"]
                        if delta.get("content"):
                            yield delta["content"]
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
