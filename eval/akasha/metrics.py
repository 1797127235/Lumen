"""Akasha 评估指标。

参考 akashic-agent/eval/longmemeval/metrics：
- token-level F1（主指标）
- exact match
- LLM-as-judge（语义正确性）
"""

from __future__ import annotations

import re
import string
from collections import Counter
from typing import Any

from lib.llm.client import LLMClient

_JUDGE_PROMPT = """\
你是一个长期记忆基准测试的严格评判员。

标准答案（gold）描述了用户的偏好或事实。
预测答案（predicted）是 agent 实际给出的回答。

问题：{question}
标准答案：{gold}
预测答案：{predicted}

判断规则：只有当预测答案包含标准答案中的具体偏好或事实时才判为正确。预测答案可以附带简短解释，只要核心事实正确且没有被 contradict。

如果预测答案向用户询问本应已记住的信息，或者给出忽略用户具体偏好的泛泛回答，则判为错误。

请只回复一个字：是 或 否。"""


def _normalise(text: str) -> str:
    text = text.lower()
    text = text.translate(str.maketrans("", "", string.punctuation))
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _tokenise(text: str) -> list[str]:
    import jieba

    normalised = _normalise(text)
    # 对中文使用 jieba 分词，对英文/数字保持空格分词
    tokens: list[str] = []
    for token in jieba.lcut(normalised):
        token = token.strip()
        if token:
            tokens.append(token)
    return tokens


def token_f1(pred: str, gold: str) -> float:
    """token-level F1，与 SQuAD / LoCoMo 一致。"""
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
    """归一化后精确匹配。"""
    return _normalise(pred) == _normalise(gold)


async def judge_answer(
    client: LLMClient,
    *,
    question: str,
    gold: str,
    predicted: str,
) -> bool:
    """LLM-as-judge：判断预测答案是否在语义上等价于标准答案。"""
    if not predicted or not predicted.strip():
        return False

    prompt = _JUDGE_PROMPT.format(
        question=question.strip(),
        gold=gold.strip(),
        predicted=predicted.strip(),
    )
    try:
        resp = await client.chat(
            messages=[
                {"role": "system", "content": "你只回复一个字：是 或 否。不要解释。"},
                {"role": "user", "content": prompt},
            ],
            max_tokens=64,
        )
        verdict = str(resp.content or "").strip().lower()
        return verdict.startswith("yes") or verdict.startswith("是")
    except Exception:
        return False


def score_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """计算汇总指标。"""
    by_type: dict[str, list[dict[str, Any]]] = {}
    for r in results:
        qt = r.get("question_type") or "unknown"
        by_type.setdefault(qt, []).append(r)

    def _agg(items: list[dict[str, Any]]) -> dict[str, Any]:
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
        result: dict[str, Any] = {
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
