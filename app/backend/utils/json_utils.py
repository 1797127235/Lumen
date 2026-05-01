"""通用工具 — JSON 鲁棒解析"""

import json
import re
import logging

logger = logging.getLogger(__name__)

# 匹配 <think>...</think>、<reasoning>...</reasoning>
_THINKING_RE = re.compile(r"<(think|thinking|reasoning)>.*?</\1>", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def parse_llm_json(text: str) -> dict:
    """从 LLM 响应中鲁棒解析 JSON 对象"""
    if not text:
        return {}

    cleaned = _THINKING_RE.sub("", text)

    fence_match = _FENCE_RE.search(cleaned)
    candidate = fence_match.group(1).strip() if fence_match else cleaned.strip()

    start = candidate.find("{")
    end = candidate.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = candidate[start : end + 1]

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        logger.warning("JSON parse failed, raw: %.200s", text)
        return {}
