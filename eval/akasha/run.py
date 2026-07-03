"""Akasha 端到端记忆质量评估入口。

用法：
    python -m eval.akasha.run \
        --data eval/akasha/data/sample_longmemeval.json \
        --workspace /tmp/akasha_eval \
        --judge

需要：
- embedding 配置（使用 core.config 中的全局 embedding 设置）
- LLM 配置（同样使用 core.config 中的全局 LLM 设置）
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding="utf-8")

from core.config import get_settings
from lib.llm.client import LLMClient
from lib.llm.embeddings import build_embedding_client
from lib.memory.builtins.akasha.engine import AkashaEngine

from .dataset import SUPPORTED_QUESTION_TYPES, load_dataset
from .ingest import ingest_instance
from .metrics import judge_answer, score_results
from .qa_runner import run_qa_instance


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run LongMemEval-style benchmark against Lumen Akasha engine.")
    parser.add_argument("--data", required=True, type=Path, help="Path to LongMemEval JSON")
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path("/tmp/akasha_eval"),
        help="Workspace directory",
    )
    parser.add_argument("--output", type=Path, default=None, help="Output JSON path")
    parser.add_argument("--limit", type=int, default=0, help="Only first N instances")
    parser.add_argument("--judge", action="store_true", help="Run LLM-as-judge")
    parser.add_argument("--ingest-only", action="store_true", help="Only ingest, skip QA")
    parser.add_argument("--qa-only", action="store_true", help="Skip ingest (DB must exist)")
    parser.add_argument("--type", dest="question_type", default=None, help="Filter question type")
    return parser


def _reset_workspace(workspace: Path) -> None:
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True, exist_ok=True)


async def _run(args: argparse.Namespace) -> None:
    settings = get_settings()
    instances = load_dataset(args.data)

    if args.question_type:
        if args.question_type not in SUPPORTED_QUESTION_TYPES:
            choices = ", ".join(SUPPORTED_QUESTION_TYPES)
            print(f"ERROR: unsupported --type {args.question_type!r}; choices: {choices}")
            sys.exit(1)
        instances = [i for i in instances if i.question_type == args.question_type]

    if args.limit > 0:
        instances = instances[: args.limit]

    print(f"Loaded {len(instances)} instances")

    if not args.qa_only:
        _reset_workspace(args.workspace)
    args.workspace.mkdir(parents=True, exist_ok=True)

    db_path = args.workspace / "akasha.db"

    embedder = build_embedding_client()
    if embedder is None:
        print("ERROR: failed to build embedding client")
        sys.exit(1)

    engine = AkashaEngine(
        user_id="akasha_eval",
        config={"db_path": str(db_path)},
        embedder=embedder,
    )

    client = LLMClient(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
    )

    try:
        # Ingest
        if not args.qa_only:
            print("\nIngesting haystack sessions...")
            for inst in instances:
                n_turns = await ingest_instance(engine, inst)
                print(f"  {inst.question_id}: {n_turns} turns")
            print("Ingest complete.\n")

        if args.ingest_only:
            print("Ingest-only complete.")
            return

        # QA
        print("Running QA...")
        results: list[dict] = []
        for idx, inst in enumerate(instances, 1):
            print(f"  [{idx}/{len(instances)}] {inst.question_id} ...", end=" ", flush=True)
            result = await run_qa_instance(engine, inst, client)

            if args.judge and not result.get("error"):
                result["judge_correct"] = await judge_answer(
                    client,
                    question=inst.question,
                    gold=inst.answer,
                    predicted=result["predicted_answer"],
                )

            results.append(result)
            print(f"F1={_f1(result):.2f}", end="")
            if result.get("error"):
                print(f" ERR={result['error'][:60]}")
            else:
                print()

        scores = score_results(results)
        overall = scores["overall"]

        print("\n" + "=" * 60)
        print("Results")
        print("=" * 60)
        print(f"Overall F1:    {overall['f1']:.2%}")
        print(f"Overall EM:    {overall['em']:.2%}")
        if overall.get("judge_acc") is not None:
            print(f"Judge Acc:     {overall['judge_acc']:.2%}")
        print(f"Instances:     {overall['n']}")
        print(f"Errors:        {overall['errors']}")

        print("\nBy question type:")
        for qt, score in sorted(scores["by_type"].items()):
            print(f"  {qt:30s} F1={score['f1']:.2%} EM={score['em']:.2%} n={score['n']}")

        # Save
        output = args.output
        if output is None:
            results_dir = Path(__file__).parent / "results"
            results_dir.mkdir(exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            output = results_dir / f"{ts}.json"

        payload = {
            "timestamp": datetime.now().isoformat(),
            "data": str(args.data),
            "workspace": str(args.workspace),
            "judge": args.judge,
            "scores": scores,
            "results": results,
        }
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nSaved → {output}")

    finally:
        engine.close()
        await embedder.close()


def _f1(result: dict) -> float:
    from .metrics import token_f1

    if result.get("error"):
        return 0.0
    return token_f1(result["predicted_answer"], result["gold_answer"])


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
