"""Provider 模型列表验证 — 过滤无效/不安全的模型 ID"""

from __future__ import annotations

from typing import Any

# 已知的不安全/测试用模型 ID 前缀黑名单
_INVALID_MODEL_PREFIXES = (
    "test-",
    "mock-",
    "dummy-",
    "internal-",
    "deprecated-",
)


def filter_discovered_models(
    provider_name: str,
    models: list[dict[str, Any]],
    base_url: str = "",
) -> tuple[list[dict[str, Any]], list[str]]:
    """过滤并规范化远程返回的模型列表

    Args:
        provider_name: Provider 标识名
        models: 原始模型列表，每项至少含 "id"
        base_url: 用于判断本地服务（如 Ollama）

    Returns:
        (filtered_models, ignored_model_ids)
    """
    filtered: list[dict[str, Any]] = []
    ignored: list[str] = []

    for m in models:
        model_id = m.get("id") or m.get("modelId") or ""
        if not model_id:
            continue

        # 过滤测试用/内部模型
        if model_id.lower().startswith(_INVALID_MODEL_PREFIXES):
            ignored.append(model_id)
            continue

        # 规范化条目
        entry = {
            "id": model_id,
            "name": m.get("name") or m.get("displayName") or model_id,
            "context": m.get("context") or m.get("max_input_tokens") or m.get("inputTokenLimit"),
            "maxOutput": m.get("maxOutput") or m.get("maxOutputTokens") or m.get("outputTokenLimit"),
        }

        # 补充能力标记（如果远程返回了）
        for flag in ("image", "video", "reasoning"):
            if flag in m:
                entry[flag] = bool(m[flag])

        filtered.append(entry)

    return filtered, ignored
