"""Provider HTTP 客户端工具 — 认证头构建、连通性探测"""

from __future__ import annotations

from typing import Any

import httpx

from shared.logging import get_logger

logger = get_logger(__name__)


# ── 认证头构建 ──────────────────────────────────────────────


def build_auth_headers(api: str, api_key: str, *, allow_missing: bool = False) -> dict[str, str]:
    """根据 API 类型构建认证请求头

    Args:
        api: API 类型标识，如 "openai-completions", "anthropic-messages", "google-generative-ai"
        api_key: API 密钥
        allow_missing: 是否允许缺失 api_key（本地服务如 Ollama）

    Returns:
        请求头字典
    """
    if not api_key and not allow_missing:
        return {}

    if api == "anthropic-messages":
        return {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
    if api == "google-generative-ai":
        # Google 使用 URL query param 传 key，此处返回空头
        return {}

    # 默认 OpenAI 兼容格式
    return {"Authorization": f"Bearer {api_key}"}


# ── 连通性探测 ──────────────────────────────────────────────


async def probe_provider(base_url: str, api: str | None, api_key: str) -> dict[str, Any]:
    """探测 Provider 是否可用

    发送一个最小请求验证连通性和认证。
    Returns {"ok": bool, "latency_ms": int, "error": str, ...}
    """
    import time

    start = time.time()
    url = base_url.rstrip("/")

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # 策略：优先尝试 /models（OpenAI 兼容），失败则降级为简单 GET
            headers = build_auth_headers(api or "openai-completions", api_key, allow_missing=True)

            # OpenAI 兼容：尝试 /models
            models_url = f"{url}/models"
            resp = await client.get(models_url, headers=headers)

            if resp.status_code == 200:
                latency = int((time.time() - start) * 1000)
                return {"ok": True, "latency_ms": latency}

            if resp.status_code in (401, 403):
                latency = int((time.time() - start) * 1000)
                return {
                    "ok": False,
                    "latency_ms": latency,
                    "error": f"认证失败 (HTTP {resp.status_code}): {resp.reason_phrase}",
                }

            # 其他状态码也尝试继续
            latency = int((time.time() - start) * 1000)
            return {
                "ok": False,
                "latency_ms": latency,
                "error": f"HTTP {resp.status_code}: {resp.reason_phrase}",
            }

    except httpx.TimeoutException:
        latency = int((time.time() - start) * 1000)
        return {"ok": False, "latency_ms": latency, "error": "请求超时"}
    except httpx.ConnectError as exc:
        latency = int((time.time() - start) * 1000)
        return {"ok": False, "latency_ms": latency, "error": f"连接失败: {exc}"}
    except Exception as exc:
        latency = int((time.time() - start) * 1000)
        return {"ok": False, "latency_ms": latency, "error": f"{type(exc).__name__}: {exc}"}
