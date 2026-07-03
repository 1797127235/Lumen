"""Akasha 图记忆引擎质量评估脚本。

用法：
    python scripts/eval_akasha_quality.py

需要配置 embedding（默认使用 core.config 中的全局 embedding 设置）。
脚本会：
1. 把若干轮模拟对话写入临时 Akasha DB
2. 用一组查询测试召回质量
3. 输出 Recall@K、平均排名、误召率等指标
"""

from __future__ import annotations

import asyncio
import io
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 强制 stdout 使用 UTF-8，避免 Windows 控制台中文乱码
if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding="utf-8")

from lib.llm.embeddings import build_embedding_client
from lib.memory.builtins.akasha.engine import AkashaEngine


@dataclass
class Turn:
    user: str
    assistant: str
    tags: list[str]
    facts: list[str]


@dataclass
class Query:
    text: str
    expected_keywords: list[str]
    should_recall: bool = True


# 模拟对话历史：覆盖多个主题，每轮带标签和关键事实
DIALOGUE: list[Turn] = [
    Turn(
        user="我最近在看新工作机会",
        assistant="你在看什么样的方向？",
        tags=["工作", "求职"],
        facts=["用户正在看新工作机会"],
    ),
    Turn(
        user="想做 AI  infra 相关的东西，后端偏分布式",
        assistant="分布式系统和 LLM 推理优化现在需求挺大的",
        tags=["工作", "AI", "分布式"],
        facts=["用户想做 AI infra / 后端分布式方向"],
    ),
    Turn(
        user="我周末打算去杭州爬山",
        assistant="杭州周边有很多不错的路线，你准备去哪个山？",
        tags=["旅行", "杭州", "爬山"],
        facts=["用户周末打算去杭州爬山"],
    ),
    Turn(
        user="想去九溪或者龙井那边走一圈",
        assistant="九溪到龙井的徒步路线很舒服，强度也不大",
        tags=["旅行", "杭州", "九溪", "龙井"],
        facts=["用户想去九溪或龙井徒步"],
    ),
    Turn(
        user="我最近在学习 Rust",
        assistant="Rust 的 ownership 系统确实需要时间适应",
        tags=["学习", "Rust"],
        facts=["用户最近在学习 Rust"],
    ),
    Turn(
        user="Rust 的 async runtime 还是有点难理解",
        assistant="可以从 tokio 的文档和 mini-redis 示例入手",
        tags=["学习", "Rust", "tokio"],
        facts=["用户觉得 Rust async runtime 难理解"],
    ),
    Turn(
        user="我女朋友对猫过敏",
        assistant="那家里可能不太适合养猫了",
        tags=["生活", "宠物", "过敏"],
        facts=["用户女朋友对猫过敏"],
    ),
    Turn(
        user="所以我们想养一只柯基",
        assistant="柯基挺适合城市养的，掉毛期需要多梳理",
        tags=["生活", "宠物", "柯基"],
        facts=["用户想养一只柯基"],
    ),
    Turn(
        user="我最近在调整饮食结构",
        assistant="有什么具体目标吗？减脂还是增肌？",
        tags=["健康", "饮食"],
        facts=["用户最近在调整饮食结构"],
    ),
    Turn(
        user="想减脂，准备把精制碳水换成粗粮",
        assistant="糙米、燕麦、红薯都是不错的替代",
        tags=["健康", "饮食", "减脂", "粗粮"],
        facts=["用户减脂，准备把精制碳水换成粗粮"],
    ),
    Turn(
        user="我下个月要去日本出差",
        assistant="去哪个城市？东京还是大阪？",
        tags=["工作", "出差", "日本"],
        facts=["用户下个月要去日本出差"],
    ),
    Turn(
        user="先去东京一周，然后飞大阪见客户",
        assistant="行程挺紧的，记得提前订新干线",
        tags=["工作", "出差", "日本", "东京", "大阪"],
        facts=["用户东京出差一周，之后去大阪见客户"],
    ),
]

QUERIES: list[Query] = [
    Query(
        text="我以后端分布式的工作该怎么准备",
        expected_keywords=["AI", "infra", "分布式", "后端"],
    ),
    Query(
        text="杭州周末有什么推荐路线",
        expected_keywords=["杭州", "九溪", "龙井", "徒步"],
    ),
    Query(
        text="Rust 学习遇到瓶颈怎么办",
        expected_keywords=["Rust", "async", "runtime", "tokio"],
    ),
    Query(
        text="我能养猫吗",
        expected_keywords=["猫", "过敏"],
    ),
    Query(
        text="我想养宠物有什么建议",
        expected_keywords=["柯基", "宠物", "养"],
    ),
    Query(
        text="减脂期主食怎么吃",
        expected_keywords=["减脂", "粗粮", "碳水", "精制"],
    ),
    Query(
        text="日本出差的行程怎么安排",
        expected_keywords=["日本", "东京", "大阪", "出差"],
    ),
    Query(
        text="量子计算机最新进展",
        expected_keywords=[],
        should_recall=False,
    ),
]


def _match_card(card: dict, query: Query) -> tuple[bool, str]:
    """判断召回卡片是否与查询预期相关。

    使用关键词匹配：卡片内容包含任意一个预期关键词即视为相关。
    """
    user_msg = (card.get("user_message") or "").lower()
    assistant_msg = (card.get("assistant_preview") or "").lower()
    combined = user_msg + " " + assistant_msg

    for kw in query.expected_keywords:
        if kw.lower() in combined:
            return True, f"命中关键词: {kw}"

    return False, ""


async def main() -> None:
    embedder = build_embedding_client()
    if embedder is None:
        print("无法构建 embedding client，请检查配置")
        return

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    engine = AkashaEngine(
        user_id="eval_user",
        config={
            "db_path": str(db_path),
            "dense_top_k": 10,
            "ripple_top_k": 10,
            "activate_limit": 8,
        },
        embedder=embedder,
    )

    try:
        print(f"写入 {len(DIALOGUE)} 轮对话到 {db_path}\n")
        for i, turn in enumerate(DIALOGUE):
            await engine.commit_turn(
                session_key="eval_sess",
                user_msg=turn.user,
                assistant_msg=turn.assistant,
                user_msg_id=f"eval_sess:u:{i}",
                assistant_msg_id=f"eval_sess:a:{i}",
                seq=i,
            )

        total = len(QUERIES)
        hits_at_1 = 0
        hits_at_3 = 0
        hits_at_5 = 0
        false_positives = 0
        total_relevant_returned = 0
        total_returned = 0
        reciprocal_ranks: list[float] = []

        print("=" * 60)
        print("召回测试结果")
        print("=" * 60)

        for query in QUERIES:
            result = await engine.query("eval_sess", query.text)
            cards = result.cards

            first_relevant_rank: int | None = None
            relevant_count = 0
            for rank, card in enumerate(cards, start=1):
                is_relevant, reason = _match_card(card, query)
                if is_relevant:
                    relevant_count += 1
                    if first_relevant_rank is None:
                        first_relevant_rank = rank

            returned_count = len(cards)
            total_returned += returned_count
            total_relevant_returned += relevant_count

            if first_relevant_rank == 1:
                hits_at_1 += 1
            if first_relevant_rank is not None and first_relevant_rank <= 3:
                hits_at_3 += 1
            if first_relevant_rank is not None and first_relevant_rank <= 5:
                hits_at_5 += 1

            if first_relevant_rank is not None:
                reciprocal_ranks.append(1.0 / first_relevant_rank)
            else:
                reciprocal_ranks.append(0.0)

            if not query.should_recall and returned_count > 0:
                false_positives += 1

            status = "HIT" if first_relevant_rank is not None else "MISS"
            print(f"\n[{status}] 查询: {query.text}")
            print(f"    召回卡片数: {returned_count}, 相关卡片数: {relevant_count}")
            if first_relevant_rank:
                print(f"    首个相关排名: {first_relevant_rank}")
            for rank, card in enumerate(cards, start=1):
                marker = " *" if _match_card(card, query)[0] else ""
                preview = (card.get("user_message") or "")[:60]
                print(f"      #{rank} [{card.get('lane', '?')}] {preview}{marker}")

        print("\n" + "=" * 60)
        print("汇总指标")
        print("=" * 60)
        print(f"查询总数: {total}")
        print(f"Recall@1:  {hits_at_1}/{total} = {hits_at_1/total:.2%}")
        print(f"Recall@3:  {hits_at_3}/{total} = {hits_at_3/total:.2%}")
        print(f"Recall@5:  {hits_at_5}/{total} = {hits_at_5/total:.2%}")
        print(f"MRR:       {sum(reciprocal_ranks)/len(reciprocal_ranks):.3f}")
        print(f"平均召回数: {total_returned/total:.1f}")
        print(f"相关召回占比: {total_relevant_returned/max(1,total_returned):.2%}")
        print(f"误召查询数 (should_recall=False 但返回结果): {false_positives}")

    finally:
        engine.close()
        await embedder.close()
        db_path.unlink(missing_ok=True)


if __name__ == "__main__":
    asyncio.run(main())
