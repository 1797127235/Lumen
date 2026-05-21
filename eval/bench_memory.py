"""Lumen 记忆系统端到端 Benchmark — CLI 入口。

Usage:
  # 全量跑（需准备 longmemeval_akashic.json）
  python -m eval.bench_memory \
      --data eval/data/longmemeval_akashic.json \
      --workspace /tmp/lumen_bench

  # Smoke test（只跑 3 题）
  python -m eval.bench_memory ... --limit 3

  # 只跑 single-session-user
  python -m eval.bench_memory ... --type single-session-user

  # 断点续跑（复用已有结果）
  python -m eval.bench_memory ... --resume-auto
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

# 确保 benchmark 进程能读取项目根目录的 .env
# （core/config.py 的 env_file 路径配置有偏差，手动补偿）
_project_root = Path(__file__).parents[1]
_env_path = _project_root / ".env"
if _env_path.exists():
    load_dotenv(_env_path, override=True)

from eval.dataset import load_dataset  # noqa: E402
from eval.ingest import ingest_instance  # noqa: E402
from eval.metrics import exact_match, judge_answer, score_results, token_f1  # noqa: E402
from eval.qa_runner import run_qa_instance  # noqa: E402
from eval.runtime import close_runtime, create_runtime  # noqa: E402

logger = logging.getLogger("eval.bench_memory")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run memory benchmark against the Lumen agent runtime.")
    p.add_argument("--data", required=True, type=Path, help="Path to dataset JSON")
    p.add_argument("--workspace", type=Path, default=Path("/tmp/lumen_bench"), help="Workspace dir")
    p.add_argument("--output", type=Path, default=None, help="Output JSON path")
    p.add_argument("--limit", type=int, default=0, help="Only first N instances")
    p.add_argument("--type", type=str, default="", help="Filter by question_type")
    p.add_argument("--resume-auto", action="store_true", help="Reuse existing results")
    p.add_argument("--ingest-only", action="store_true", help="Skip QA")
    p.add_argument("--qa-only", action="store_true", help="Skip ingest")
    p.add_argument("--timeout", type=float, default=180.0, help="QA timeout (s)")
    p.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    return p


async def _run_instance(instance, workspace: Path, args) -> dict | None:
    """Run ingest + QA + judge for a single instance."""
    qid = instance.question_id
    ws = workspace / qid
    result_path = ws / "result.json"

    # resume-auto: 已有结果直接复用
    if args.resume_auto and result_path.exists():
        logger.info("[%s] resume: reuse existing result", qid)
        return json.loads(result_path.read_text(encoding="utf-8"))

    # qa-only: 必须有 ingest_state
    if args.qa_only and not (ws / "ingest_state.json").exists():
        logger.warning("[%s] qa-only skipped: no ingest state", qid)
        return None

    rt = await create_runtime(ws)
    try:
        # ── ingest ──
        if not args.qa_only:
            await ingest_instance(rt, instance, force=args.ingest_only)

        if args.ingest_only:
            return None

        # ── QA ──
        qa_result = await run_qa_instance(rt, instance, timeout_s=args.timeout)

        # ── judge / metrics ──
        if not qa_result.get("error"):
            judge_ok = await judge_answer(
                question=instance.question,
                gold=instance.answer,
                predicted=qa_result["predicted_answer"],
            )
            qa_result["judge_correct"] = judge_ok
            qa_result["f1"] = token_f1(qa_result["predicted_answer"], instance.answer)
            qa_result["em"] = exact_match(qa_result["predicted_answer"], instance.answer)

        # 落盘
        result_path.write_text(json.dumps(qa_result, ensure_ascii=False, indent=2), encoding="utf-8")
        return qa_result
    finally:
        await close_runtime(rt)


async def main() -> int:
    args = _build_parser().parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    # ── load dataset ──
    if not args.data.exists():
        logger.error("Dataset not found: %s", args.data)
        return 1

    dataset = load_dataset(args.data)
    logger.info("Loaded %d instances from %s", len(dataset), args.data)

    if args.type:
        dataset = [d for d in dataset if d.question_type == args.type]
        logger.info("Filtered to %d instances (type=%s)", len(dataset), args.type)

    if args.limit:
        dataset = dataset[: args.limit]
        logger.info("Limited to first %d instances", len(dataset))

    # ── main loop ──
    results: list[dict] = []
    for idx, instance in enumerate(dataset, 1):
        logger.info("[%d/%d] %s (%s)", idx, len(dataset), instance.question_id, instance.question_type)
        result = await _run_instance(instance, args.workspace, args)
        if result is not None:
            results.append(result)
            logger.info(
                "[%s] predicted=%r gold=%r judge=%s f1=%.3f",
                instance.question_id,
                result.get("predicted_answer", "")[:80],
                instance.answer[:80],
                result.get("judge_correct"),
                result.get("f1", 0.0),
            )

    # ── scoring ──
    scores = score_results(results)
    summary = {
        "meta": {
            "dataset": str(args.data),
            "workspace": str(args.workspace),
            "total": len(dataset),
            "evaluated": len(results),
            "timestamp": datetime.now().isoformat(),
        },
        "scores": scores,
        "results": results,
    }

    # ── output ──
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("Results written to %s", args.output)

    print(json.dumps(scores, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
