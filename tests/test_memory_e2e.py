"""记忆系统端到端测试 — 验证「用户说了→系统记了→下次能召回」。

核心链路：
  写入（_memory_add）→ 存储（memory.md）→ 注入（system_prompt_block）

使用 tmpdir 隔离，monkeypatch _BASE_MEMORY_DIR 指向 tmpdir。
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from lib.memory.builtin_provider import BuiltinMemoryProvider
from lib.memory.manager import MemoryManager
from lib.memory.markdown import AsyncMarkdownStore
from lib.memory.understanding import _LAST_UPDATE, _PENDING_TASKS


async def _cancel_pending_bg_tasks():
    """取消所有 pending 的后台 understanding 任务，避免 'Task was destroyed' 警告。"""
    for key in list(_PENDING_TASKS.keys()):
        try:
            task = _PENDING_TASKS.pop(key, None)
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, RuntimeError):
                    await task
        except RuntimeError:
            pass


@pytest.fixture(autouse=True)
def isolate_memory(tmp_path: Path, monkeypatch):
    """将记忆存储重定向到 tmpdir，清空全局防抖状态。"""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    monkeypatch.setattr("lib.memory.markdown._BASE_MEMORY_DIR", memory_dir)
    monkeypatch.setattr("lib.tools.memory._store", AsyncMarkdownStore())
    # 抑制后台 understanding 刷新（避免 'Task was destroyed' 警告）
    monkeypatch.setattr("lib.tools.memory._bg_refresh_understanding", AsyncMock())
    # 清空防抖状态
    _LAST_UPDATE.clear()
    for key in list(_PENDING_TASKS.keys()):
        try:
            task = _PENDING_TASKS.pop(key, None)
            if task and not task.done():
                task.cancel()
        except RuntimeError:
            pass
    yield
    # 清理
    _LAST_UPDATE.clear()
    for key in list(_PENDING_TASKS.keys()):
        try:
            task = _PENDING_TASKS.pop(key, None)
            if task and not task.done():
                task.cancel()
        except RuntimeError:
            pass


def _deps(user_id: str = "e2e_test_user") -> SimpleNamespace:
    """构建工具函数需要的最小 deps。"""
    return SimpleNamespace(user_id=user_id)


def _result_text(result) -> str:
    """从 tool 返回值提取文本。"""
    if hasattr(result, "return_value"):
        return result.return_value
    return str(result)


# ═══════════════════════════════════════════════════════════════
# 测试 1：该记的记住了 — 写入→存储→注入完整链路
# ═══════════════════════════════════════════════════════════════


async def test_write_store_recall():
    """用户说'我订阅了 Simon 的博客' → 写入 memory.md → system prompt 包含这条记忆。"""
    from lib.tools.memory import _memory_add

    deps = _deps()

    # 1. 写入
    args = {
        "action": "add",
        "target": "memory",
        "category": "fact",
        "content": "已订阅 Simon Willison 的博客",
    }
    result = await _memory_add(args, deps, deps.user_id, "memory", args["content"])
    assert "已记录" in _result_text(result)

    # 2. 验证存储
    store = AsyncMarkdownStore()
    content = await store.read_memory(deps.user_id)
    assert "Simon Willison" in content
    assert "[fact]" in content

    # 3. 验证注入 — 新对话的 system prompt 应包含这条记忆
    provider = BuiltinMemoryProvider()
    # BuiltinMemoryProvider 也用 _BASE_MEMORY_DIR，已被 monkeypatch
    snapshot = await provider.system_prompt_block(user_id=deps.user_id)
    assert "Simon Willison" in snapshot, f"system prompt 应包含刚写入的记忆，实际: {snapshot[:200]}"


# ═══════════════════════════════════════════════════════════════
# 测试 2：记对了 — category 分类准确性
# ═══════════════════════════════════════════════════════════════


async def test_category_fact_vs_intent():
    """'我已经买了 iPhone 16' 是 fact 不是 intent。'打算学 Rust' 是 intent。"""
    from lib.tools.memory import _memory_add

    deps = _deps()

    # fact: 已完成的动作
    await _memory_add(
        {"action": "add", "target": "memory", "category": "fact", "content": "已购买 iPhone 16 Pro"},
        deps,
        deps.user_id,
        "memory",
        "已购买 iPhone 16 Pro",
    )

    # intent: 计划中的事
    await _memory_add(
        {"action": "add", "target": "memory", "category": "intent", "content": "打算学习 Rust 编程语言"},
        deps,
        deps.user_id,
        "memory",
        "打算学习 Rust 编程语言",
    )

    store = AsyncMarkdownStore()
    content = await store.read_memory(deps.user_id)

    # fact 和 intent 都在，且分类标签正确
    lines = content.splitlines()
    fact_line = next(line for line in lines if "iPhone" in line)
    intent_line = next(line for line in lines if "Rust" in line)
    assert "[fact]" in fact_line, f"已购买的 iPhone 应标记为 fact，实际: {fact_line}"
    assert "[intent]" in intent_line, f"打算学的 Rust 应标记为 intent，实际: {intent_line}"


# ═══════════════════════════════════════════════════════════════
# 测试 3：矛盾会更新 — replace 替换旧条目
# ═══════════════════════════════════════════════════════════════


async def test_contradiction_replaces_old():
    """先记'想订阅 Simon 博客'(intent)，后说'已订阅' → 应替换为 fact。"""
    from lib.tools.memory import _memory_add, _memory_replace_remove

    deps = _deps()

    # 第一步：写入旧记忆（intent）
    await _memory_add(
        {"action": "add", "target": "memory", "category": "intent", "content": "想订阅 Simon Willison 的博客"},
        deps,
        deps.user_id,
        "memory",
        "想订阅 Simon Willison 的博客",
    )

    store = AsyncMarkdownStore()
    content_before = await store.read_memory(deps.user_id)
    assert "想订阅" in content_before

    # 第二步：用户纠正 — 替换为 fact
    result = await _memory_replace_remove(
        action="replace",
        user_id=deps.user_id,
        target="memory",
        old_text="想订阅 Simon",
        new_content="已订阅 Simon Willison 的博客",
    )
    assert "已替换" in _result_text(result)

    # 第三步：验证只剩新记忆，旧记忆不存在
    content_after = await store.read_memory(deps.user_id)
    assert "已订阅" in content_after
    assert "想订阅" not in content_after


# ═══════════════════════════════════════════════════════════════
# 测试 4：不该记的没记 — memory_search 对无关内容不返回
# ═══════════════════════════════════════════════════════════════


async def test_search_only_returns_relevant():
    """写入两条记忆到不同段落（双换行分隔），搜索只返回匹配的段落。

    _search 按段落（\n\n 分割）匹配关键词。两条记忆默认写在同一个
    ## Long-term notes 段落里（\n 分隔），所以搜 "Simon" 会同时返回
    VS Code。这里通过手动构造不同段落来验证段落级过滤逻辑本身是正确的。
    """
    from lib.memory.markdown import AsyncMarkdownStore
    from lib.tools.memory import _search

    deps = _deps()
    store = AsyncMarkdownStore()

    # 直接构造两个段落，模拟长时间跨度后两次写入的场景
    # （append_memory_entry 追加的条目在同一段落内，所以用 write_memory 手动构造）
    content = (
        "# 关于你\n\n"
        "## Long-term notes\n\n"
        "- 2026-06-07 — [fact] 已订阅 Simon Willison 的博客\n\n"
        "## Work preferences\n\n"
        "- 2026-06-07 — [fact] 使用的编辑器是 VS Code\n"
    )
    await store.write_memory(deps.user_id, content)

    # 搜索 "Simon" — 只应匹配第一个段落
    result = await _search({"query": "Simon"}, deps)
    text = _result_text(result)
    assert "Simon" in text, "搜索 Simon 应返回 Simon 相关记忆"
    assert "VS Code" not in text, "搜索 Simon 不应返回 VS Code 相关记忆"

    # 搜索 "编辑器" — 只应匹配第二个段落
    result2 = await _search({"query": "编辑器"}, deps)
    text2 = _result_text(result2)
    assert "VS Code" in text2, "搜索编辑器应返回 VS Code 相关记忆"
    assert "Simon" not in text2, "搜索编辑器不应返回 Simon 相关记忆"

    # 搜索不存在的内容 — 应返回"未找到"
    result3 = await _search({"query": "潜水"}, deps)
    text3 = _result_text(result3)
    assert "未找到" in text3, "搜索不相关内容应返回'未找到'"


# ═══════════════════════════════════════════════════════════════
# 测试 5：冻结语义 — 对话中写入，当前 system prompt 不变
# ═══════════════════════════════════════════════════════════════


async def test_frozen_semantics():
    """对话 A 开始时冻结 system prompt，对话中写入记忆不影响当前 prompt。

    但调用 system_prompt_block()（模拟新对话）应能看到新记忆。
    """
    from lib.tools.memory import _memory_add

    deps = _deps()

    # 1. 对话 A 开始 — 记忆为空，system prompt 为空
    provider = BuiltinMemoryProvider()
    prompt_a_before = await provider.system_prompt_block(user_id=deps.user_id)
    assert prompt_a_before == "" or prompt_a_before.strip() == ""

    # 2. 对话 A 中写入记忆
    await _memory_add(
        {"action": "add", "target": "memory", "category": "fact", "content": "已订阅 Simon Willison 的博客"},
        deps,
        deps.user_id,
        "memory",
        "已订阅 Simon Willison 的博客",
    )

    # 3. 模拟对话 A 的 system prompt 已冻结（实际是调用方按 conversation 缓存）
    #    这里验证：如果调用方再次调用 system_prompt_block（不缓存），会看到新记忆
    #    但真正冻结语义是调用方的行为（snapshot.py 的 LRU 缓存）
    #    我们验证的是：写入后立即可读（给新对话用）
    prompt_new_conversation = await provider.system_prompt_block(user_id=deps.user_id)
    assert "Simon Willison" in prompt_new_conversation, "新对话的 system prompt 应能看到对话 A 中写入的记忆"


# ═══════════════════════════════════════════════════════════════
# 测试 6：remove 删除后记忆不可召回
# ═══════════════════════════════════════════════════════════════


async def test_remove_makes_unrecallable():
    """删除一条记忆后，memory_search 和 system prompt 都不应包含它。"""
    from lib.tools.memory import _memory_add, _memory_replace_remove, _search

    deps = _deps()

    # 写入
    await _memory_add(
        {"action": "add", "target": "memory", "category": "transient", "content": "最近在加班赶项目"},
        deps,
        deps.user_id,
        "memory",
        "最近在加班赶项目",
    )

    # 确认可搜到
    result = await _search({"query": "加班"}, deps)
    assert "加班" in _result_text(result)

    # 删除
    await _memory_replace_remove(
        action="remove",
        user_id=deps.user_id,
        target="memory",
        old_text="加班",
    )

    # 验证搜不到
    result2 = await _search({"query": "加班"}, deps)
    assert "未找到" in _result_text(result2) or "加班" not in _result_text(result2)


# ═══════════════════════════════════════════════════════════════
# 测试 7：MemoryManager 编排 — 多 provider 隔离
# ═══════════════════════════════════════════════════════════════


async def test_manager_system_prompt_includes_builtin():
    """MemoryManager.build_system_prompt() 包含 builtin provider 的记忆。"""
    from lib.tools.memory import _memory_add

    deps = _deps()

    # 写入记忆
    await _memory_add(
        {"action": "add", "target": "memory", "category": "fact", "content": "已订阅 Simon Willison 的博客"},
        deps,
        deps.user_id,
        "memory",
        "已订阅 Simon Willison 的博客",
    )

    manager = MemoryManager()
    prompt = await manager.build_system_prompt(user_id=deps.user_id)
    assert "Simon Willison" in prompt, f"MemoryManager 的 system prompt 应包含记忆，实际: {prompt[:200]}"


async def test_manager_external_provider_failure_isolated():
    """外部 provider 失败不影响 builtin provider。"""
    from lib.tools.memory import _memory_add

    deps = _deps()

    # 写入记忆
    await _memory_add(
        {"action": "add", "target": "memory", "category": "fact", "content": "已订阅 Simon 博客"},
        deps,
        deps.user_id,
        "memory",
        "已订阅 Simon 博客",
    )

    manager = MemoryManager()

    # 添加一个会失败的假 provider
    class FailingProvider:
        name = "failing"

        async def system_prompt_block(self, **kwargs):
            raise ConnectionError("外部服务不可用")

    failing = FailingProvider()
    # 手动注册（绕过 add_provider 的外部限制）
    manager._providers["failing"] = failing  # type: ignore

    # build_system_prompt 应不崩溃，且仍包含 builtin 的记忆
    prompt = await manager.build_system_prompt(user_id=deps.user_id)
    assert "Simon" in prompt
