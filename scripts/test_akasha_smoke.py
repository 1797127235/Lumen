"""Akasha 冒烟验证脚本 —— 独立走真实 DashScope embedding,验证全链路。

跑法:
    python scripts/test_akasha_smoke.py

验证三件事:
1. embedding 连通 (Provider.is_available)
2. 写入 3 轮对话 (commit_turn)
3. 召回 (query) —— 打印卡片,人肉看是否合理
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

# 让脚本能 import 项目根的 lib
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 确保读到 .env —— build_embedding_client 内部走 get_settings(),会自动加载 .env
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


async def main() -> int:
    from lib.memory.builtins.akasha import Provider

    print("=" * 60)
    print("Akasha 冒烟测试 —— 真实 DashScope embedding")
    print("=" * 60)

    # 用临时 DB,不污染 ~/.lumen
    tmp_dir = Path(tempfile.mkdtemp(prefix="akasha_smoke_"))
    db_path = tmp_dir / "akasha.db"

    provider = Provider(db_path=str(db_path))
    print(f"\n[1/4] Provider.name = {provider.name}")
    print(f"      DB: {db_path}")

    # ── 步骤 2:检查 embedding 连通性 ──
    print("\n[2/4] 检查 embedding 连通性 (is_available)...")
    available = await provider.is_available()
    print(f"      is_available = {available}")
    if not available:
        print("\n❌ embedding 连不通。检查:")
        print("  - .env 里 EMBEDDING_API_KEY / EMBEDDING_BASE_URL / EMBEDDING_MODEL")
        print("  - DashScope key 是否有效、是否开通了 text-embedding-v4")
        return 1
    print("      ✅ embedding 可用")

    # ── 步骤 3:初始化 engine,写入 3 轮对话 ──
    print("\n[3/4] 初始化 engine 并写入 3 轮对话...")
    await provider.initialize(session_id="smoke-session", user_id="smoke_user")

    turns = [
        (
            "我最近在学 Rust,ownership 和 borrow checker 让我挺头疼的",
            "Rust 的所有权系统确实是新手最大的坎,建议先看 The Book 前 5 章,把 ownership、borrow、lifetime 三个概念理清楚。",
        ),
        (
            "我用 FastAPI 写后端,异步 SQLAlchemy 怎么处理 session?",
            "FastAPI 里用 dependency 注入 AsyncSession,每个请求一个 session,记得 yield + finally close。",
        ),
        (
            "今天心情不太好,工作压力很大",
            "听到你说压力大,愿意多说说吗?有时候把事情讲出来本身就能缓解一些。",
        ),
    ]

    session_key = "web:smoke-test"
    for _i, (user_msg, assistant_msg) in enumerate(turns):
        seq = provider._engine.next_turn_seq(session_key)  # type: ignore[union-attr]
        await provider.sync_turn(
            user_msg,
            assistant_msg,
            session_id=session_key,
        )
        print(f"      ✓ turn {seq}: {user_msg[:30]}...")

    # ── 步骤 4:召回测试 ──
    print("\n[4/4] 召回测试 —— 3 个不同主题的 query")

    queries = [
        "Rust 学习",
        "FastAPI 异步数据库",
        "用户情绪/压力",
    ]

    all_ok = True
    for q in queries:
        text = await provider.prefetch(q, session_id=session_key)
        cards = await _count_cards(provider, session_key, q)
        print(f"\n      query: 「{q}」")
        print(f"        cards: {cards}")
        if cards == 0:
            print("        ⚠️  无召回 —— 可能是阈值过严或 embedding 维度问题")
            all_ok = False
        else:
            print("        ✅ 召回成功")
            # 打印前两条卡片预览
            for line in text.splitlines()[:6]:
                if line.strip():
                    print(f"        {line}")

    await provider.shutdown()

    print("\n" + "=" * 60)
    if all_ok:
        print("✅ 冒烟测试通过 —— Akasha 全链路工作正常")
        print(f"   临时 DB 在: {db_path} (可手动删除 {tmp_dir})")
    else:
        print("⚠️  部分召回为空 —— 引擎跑起来了,但召回阈值可能需要调")
        print(f"   临时 DB 在: {db_path} (可手动删除 {tmp_dir})")
    print("=" * 60)
    return 0 if all_ok else 2


async def _count_cards(provider, session_key: str, query: str) -> int:
    """直接调 engine.query 拿 cards 数量(prefetch 只返回 text)。"""
    engine = provider._engine
    if engine is None:
        return 0
    result = await engine.query(session_key, query)
    return len(result.cards)


if __name__ == "__main__":
    rc = asyncio.run(main())
    sys.exit(rc)
