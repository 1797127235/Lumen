"""评分函数 — token F1 / EM / LLM judge。

直接复用 LongMemEval 的 scoring 逻辑，LLM 调用改为 career-os 的
`create_model()` + PydanticAI Agent。
"""

from __future__ import annotations

import logging
import re
import string
from collections import Counter

logger = logging.getLogger(__name__)

_JUDGE_PROMPT = """\
你是长期记忆 benchmark 的严格评判者。

黄金答案描述了用户的偏好或事实。预测答案是 agent 实际给出的回答。
问题和答案可能是中文或英文，请跨语言判断语义是否一致。

问题：{question}
黄金答案：{gold}
预测答案：{predicted}

严格判断：只有当预测答案在语义上反映了黄金答案中的具体偏好或事实时，才判为正确。
如果预测答案向用户询问记忆中本应已有的信息，或给出忽略用户具体偏好的泛泛回答，判为错误。

只回复一个词：yes 或 no。"""


# ── text normalisation ────────────────────────────────────────────────────────


def _normalise(text: str) -> str:
    text = text.lower()
    text = text.translate(str.maketrans("", "", string.punctuation))
    text = re.sub(r"\s+", " ", text).strip()
    return text


_CJK_RE = re.compile(r"[一-鿿]")


def _tokenise(text: str) -> list[str]:
    normalised = _normalise(text)
    if _CJK_RE.search(normalised):
        try:
            import jieba

            return list(jieba.cut(normalised))
        except ImportError:
            pass
    return normalised.split()


# ── per-pair metrics ──────────────────────────────────────────────────────────


def token_f1(pred: str, gold: str) -> float:
    pred_tokens = _tokenise(pred)
    gold_tokens = _tokenise(gold)
    if not pred_tokens or not gold_tokens:
        return float(pred_tokens == gold_tokens)
    common = Counter(pred_tokens) & Counter(gold_tokens)
    num_same = sum(common.values())
    if num_same == 0:
        return 0.0
    precision = num_same / len(pred_tokens)
    recall = num_same / len(gold_tokens)
    return 2 * precision * recall / (precision + recall)


def exact_match(pred: str, gold: str) -> bool:
    return _normalise(pred) == _normalise(gold)


# ── llm judge ────────────────────────────────────────────────────────────────


async def judge_answer(
    *,
    question: str,
    gold: str,
    predicted: str,
) -> bool:
    """Single LLM call: returns True if predicted is semantically correct."""
    if not predicted or not predicted.strip():
        return False
    prompt = _JUDGE_PROMPT.format(
        question=question.strip(),
        gold=gold.strip(),
        predicted=predicted.strip(),
    )
    try:
        from core.config import get_settings
        from lib.llm.client import LLMClient

        settings = get_settings()
        llm = LLMClient(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model,
        )
        messages = [
            {"role": "system", "content": "你是一个评判助手。请回答 yes 或 no。"},
            {"role": "user", "content": prompt},
        ]
        response = await llm.chat(messages=messages)
        verdict = str(response.content or "").strip().lower()
        return verdict.startswith("yes")
    except Exception as e:
        logger.warning("judge_answer failed: %s", e)
        return False


# ── dataset-level scoring ────────────────────────────────────────────────────


def score_results(results: list[dict]) -> dict:
    """Compute aggregate and per-type scores.

    Returns:
        {
            "overall": {"f1": float, "em": float, "judge_acc": float|None, "n": int, "errors": int},
            "by_type": {question_type: {...}},
        }
    """
    by_type: dict[str, list[dict]] = {}
    for r in results:
        qt = r.get("question_type") or "unknown"
        by_type.setdefault(qt, []).append(r)

    def _agg(items: list[dict]) -> dict:
        errors = sum(1 for r in items if r.get("error"))
        f1s = [0.0 if r.get("error") else token_f1(r["predicted_answer"], r["gold_answer"]) for r in items]
        ems = [
            0.0 if r.get("error") else (1.0 if exact_match(r["predicted_answer"], r["gold_answer"]) else 0.0)
            for r in items
        ]
        judged = [r for r in items if r.get("judge_correct") is not None and not r.get("error")]
        judge_acc = round(sum(1 for r in judged if r["judge_correct"]) / len(judged), 4) if judged else None
        n = len(items)
        if n == 0:
            return {"f1": 0.0, "em": 0.0, "judge_acc": None, "n": 0, "errors": 0}
        result = {
            "f1": round(sum(f1s) / n, 4),
            "em": round(sum(ems) / n, 4),
            "n": n,
            "errors": errors,
        }
        if judge_acc is not None:
            result["judge_acc"] = judge_acc
        return result

    return {
        "overall": _agg(results),
        "by_type": {qt: _agg(items) for qt, items in sorted(by_type.items())},
    }
