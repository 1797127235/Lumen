"""通用工具 — JSON 鲁棒解析与截断检测"""

import json
import logging
import re

logger = logging.getLogger(__name__)

MAX_JSON_SIZE = 1024 * 1024  # 1MB
MAX_RECURSION_DEPTH = 10

# 匹配 <think>...</think>、<reasoning>...</reasoning>
_THINKING_RE = re.compile(r"<(think|thinking|reasoning)>.*?</\1>", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)

# 检测截断：核心字段为空列表可能是 LLM 未输出完
_TRUNCATION_SENSITIVE_KEYS = {"workExperience", "education", "skills", "current_skills"}


def parse_llm_json(text: str) -> dict:
    """从 LLM 响应中鲁棒解析 JSON 对象（向后兼容别名）"""
    return extract_json(text)


def extract_json(content: str) -> dict:
    """从 LLM 响应提取 JSON，处理 thinking tags、markdown code blocks、括号匹配。"""
    if not content:
        return {}

    # 1. 处理 thinking tags
    cleaned = _THINKING_RE.sub("", content)

    # 2. 处理 markdown code blocks
    fence_match = _FENCE_RE.search(cleaned)
    candidate = fence_match.group(1).strip() if fence_match else cleaned.strip()

    # 3. 提取 JSON 对象 — 括号匹配（修复 rfind 边界 bug）
    try:
        json_str = _extract_json_object(candidate)
    except ValueError as e:
        logger.warning("JSON extraction failed: %s", e)
        return {}

    # 4. 解析
    try:
        result = json.loads(json_str)
    except json.JSONDecodeError:
        logger.warning("JSON parse failed, raw: %.200s", content)
        return {}

    # 5. 截断检测
    if _appears_truncated(result):
        logger.warning("Parsed JSON appears truncated (empty critical fields)")

    return result if isinstance(result, dict) else {}


def _extract_json_object(content: str, _depth: int = 0) -> str:
    """提取完整的 JSON 对象 — 括号匹配算法，正确处理字符串内的 }。"""
    if _depth > MAX_RECURSION_DEPTH:
        raise ValueError("Exceeded max JSON extraction recursion depth")

    if len(content) > MAX_JSON_SIZE:
        raise ValueError("Content too large for JSON extraction")

    # 找到第一个 {
    start_idx = content.find("{")
    if start_idx == -1:
        raise ValueError("No JSON object found")

    # 括号匹配：逐字符遍历，正确处理转义和字符串
    depth = 0
    in_string = False
    escape_next = False

    for i, char in enumerate(content[start_idx:], start=start_idx):
        if escape_next:
            escape_next = False
            continue
        if char == "\\":
            escape_next = True
            continue
        if char == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return content[start_idx : i + 1]

    raise ValueError("Unbalanced braces — JSON appears truncated")


def _appears_truncated(data: dict) -> bool:
    """检测 JSON 是否可能被截断：核心字段为空的列表。"""
    if not isinstance(data, dict):
        return False
    return any(key in data and data[key] == [] for key in _TRUNCATION_SENSITIVE_KEYS)
