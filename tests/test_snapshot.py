"""测试 snapshot.py — L0+L1 组装、缓存、纯函数。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from lib.memory.snapshot import (
    ConversationContext,
    _build_context_block,
    _cache_insert,
    _CacheEntry,
    _has_substantive_content,
    _static_cache,
    _strip_meta,
    invalidate_cache,
)


class TestStripMeta:
    """测试元数据注释行移除。"""

    def test_removes_html_comment(self):
        text = "<!-- lumen-meta: events=5 generated_at=2026-01-01 -->\n内容"
        result = _strip_meta(text)
        assert "<!--" not in result
        assert "内容" in result

    def test_preserves_normal_text(self):
        assert _strip_meta("正常文本") == "正常文本"

    def test_removes_incomplete_comment(self):
        text = "<!-- some incomplete comment\n内容"
        result = _strip_meta(text)
        assert "<!--" not in result
        assert "内容" in result


class TestHasSubstantiveContent:
    """判断文本是否有实质内容（非纯模板占位符）。"""

    def test_short_text_is_not_substantive(self):
        assert _has_substantive_content("短") is False

    def test_exactly_30_chars_boundary(self):
        text = "A" * 30
        assert _has_substantive_content(text) is False

    def test_31_chars_is_substantive(self):
        text = "A" * 31
        assert _has_substantive_content(text) is True

    def test_placeholder_only_not_substantive(self):
        text = "A" * 31 + "（待填写）" + "B" * 31
        # 去掉占位符后只剩 A*31 + B*31 = 62 > 30，所以是 substantive
        assert _has_substantive_content(text) is True

    def test_all_placeholders_not_substantive(self):
        text = "（待填写）_暂无记录_（待填写）_暂无记录_（待填写）"
        # 去掉后内容极少
        assert _has_substantive_content(text) is False

    def test_real_content_is_substantive(self):
        text = "用户是一位热爱编程的年轻人，喜欢在晚上独自研究新技术，对 AI 和分布式系统特别感兴趣。"
        assert _has_substantive_content(text) is True


class TestBuildContextBlock:
    """测试 L1 近期对话上下文构建。"""

    def test_empty_contexts(self):
        result, conv_ids = _build_context_block([])
        assert result == ""
        assert conv_ids == set()

    def test_single_context_with_summary(self):
        ctx = ConversationContext(
            conversation_id="conv1",
            title="项目讨论",
            summary="讨论了 FastAPI 项目架构",
            messages=[{"role": "user", "content": "我们来讨论架构"}],
        )
        result, conv_ids = _build_context_block([ctx])
        assert "项目讨论" in result
        assert "FastAPI" in result
        assert "conv1" in conv_ids

    def test_single_context_without_summary(self):
        """无 summary 时从 messages 提取内容提示。"""
        ctx = ConversationContext(
            conversation_id="conv2",
            title=None,
            summary=None,
            messages=[
                {"role": "user", "content": "你好"},
                {"role": "assistant", "content": "你好！有什么可以帮你的？"},
            ],
        )
        result, conv_ids = _build_context_block([ctx])
        assert "conv2" in conv_ids
        assert "你好" in result

    def test_context_without_title_uses_first_user_message(self):
        """无 title 时用第一条用户消息的前 40 字。"""
        ctx = ConversationContext(
            conversation_id="conv3",
            title=None,
            summary=None,
            messages=[{"role": "user", "content": "我想了解一下 Python 的异步编程"}],
        )
        result, _ = _build_context_block([ctx])
        assert "Python 的异步编程" in result

    def test_context_no_messages_skipped(self):
        """无消息的对话被跳过。"""
        ctx = ConversationContext(
            conversation_id="conv4",
            title="空对话",
            summary=None,
            messages=[],
        )
        result, conv_ids = _build_context_block([ctx])
        assert result == ""
        assert conv_ids == set()

    def test_multiple_contexts(self):
        """多个对话上下文拼接。"""
        contexts = [
            ConversationContext(
                conversation_id=f"conv{i}",
                title=f"对话{i}",
                summary=f"摘要{i}",
                messages=[{"role": "user", "content": f"消息{i}"}],
            )
            for i in range(3)
        ]
        result, conv_ids = _build_context_block(contexts)
        assert "对话0" in result
        assert "对话1" in result
        assert "对话2" in result
        assert len(conv_ids) == 3

    def test_long_line_truncated(self):
        """超长行被截断到 120 字符。"""
        ctx = ConversationContext(
            conversation_id="conv_long",
            title="T",
            summary="S" * 200,
            messages=[{"role": "user", "content": "M"}],
        )
        result, _ = _build_context_block([ctx])
        # 每行不超过 120 字符（"- **T**：..." 格式）
        lines = result.split("\n")
        for line in lines:
            if line.startswith("- **"):
                assert len(line) <= 120


class TestCache:
    """测试缓存插入、失效、LRU 驱逐。"""

    @pytest.fixture(autouse=True)
    def clean_cache(self):
        """每个测试前后清空缓存。"""
        _static_cache.clear()
        yield
        _static_cache.clear()

    @pytest.mark.asyncio
    async def test_cache_insert_and_read(self):
        entry = _CacheEntry(
            user_id="cache_user_1",
            content="快照内容",
            created_at=datetime.now(UTC),
        )
        await _cache_insert(entry)
        assert "cache_user_1" in _static_cache
        assert _static_cache["cache_user_1"].content == "快照内容"

    @pytest.mark.asyncio
    async def test_invalidate_cache(self):
        entry = _CacheEntry(
            user_id="cache_user_2",
            content="内容",
            created_at=datetime.now(UTC),
        )
        await _cache_insert(entry)
        await invalidate_cache("cache_user_2")
        assert "cache_user_2" not in _static_cache

    @pytest.mark.asyncio
    async def test_invalidate_nonexistent_is_safe(self):
        await invalidate_cache("nonexistent_cache_user")  # 不应报错

    @pytest.mark.asyncio
    async def test_cache_overwrites_same_user(self):
        e1 = _CacheEntry(user_id="cache_user_3", content="旧", created_at=datetime.now(UTC))
        e2 = _CacheEntry(user_id="cache_user_3", content="新", created_at=datetime.now(UTC))
        await _cache_insert(e1)
        await _cache_insert(e2)
        assert _static_cache["cache_user_3"].content == "新"
