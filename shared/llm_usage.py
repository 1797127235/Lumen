"""LLM usage 字段标准化 — 屏蔽各 provider 的非标准字段差异。

新增 provider 时只在两个列表里追加字段名即可，调用方无需改动。
"""

from __future__ import annotations

# provider 在 usage.details 里的非标准 cache 字段，按优先级排列
_CACHE_READ_KEYS: list[str] = [
    "prompt_cache_hit_tokens",  # DeepSeek
]
_CACHE_WRITE_KEYS: list[str] = [
    "prompt_cache_miss_tokens",  # DeepSeek
]


def _first(details: dict, keys: list[str]) -> int:
    for key in keys:
        val = details.get(key)
        if val:
            return int(val)
    return 0


def extract_usage(u) -> dict:
    """将 LLM usage 标准化为统一字典。

    优先读标准字段（Anthropic / OpenAI 原生接口），
    无值时按 _CACHE_READ/WRITE_KEYS 遍历 details（provider 特定字段）。
    """
    cache_read = u.cache_read_tokens or _first(u.details, _CACHE_READ_KEYS)
    cache_write = u.cache_write_tokens or _first(u.details, _CACHE_WRITE_KEYS)
    return {
        "input": u.input_tokens or 0,
        "output": u.output_tokens or 0,
        "cache_read": cache_read,
        "cache_write": cache_write,
    }
