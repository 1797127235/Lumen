"""测试记忆定期整理 — housekeep_memory() 纯函数 + MemoryHousekeeper 生命周期。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from lib.memory.housekeeping import (
    MemoryHousekeeper,
    _is_stale_tag,
    _parse_entry,
    housekeep_memory,
)

# ── _parse_entry ──


class TestParseEntry:
    def test_valid_entry(self):
        result = _parse_entry("- 2026-05-01 — [fact] 用户喜欢咖啡")
        assert result is not None
        date_str, category, content = result
        assert date_str == "2026-05-01"
        assert category == "fact"
        assert content == "用户喜欢咖啡"

    def test_valid_entry_with_spaces(self):
        result = _parse_entry("- 2026-06-03 — [intent] 打算学 Rust 语言")
        assert result is not None
        assert result[1] == "intent"
        assert result[2] == "打算学 Rust 语言"

    def test_stale_tag(self):
        result = _parse_entry("- 2026-04-01 — [intent?stale] 学过 Python")
        assert result is not None
        assert result[1] == "intent?stale"

    def test_invalid_no_dash_prefix(self):
        assert _parse_entry("2026-05-01 — [fact] content") is None

    def test_invalid_no_brackets(self):
        assert _parse_entry("- 2026-05-01 — content") is None

    def test_invalid_no_em_dash(self):
        assert _parse_entry("- 2026-05-01 [fact] content") is None

    def test_invalid_empty(self):
        assert _parse_entry("") is None

    def test_header_line(self):
        assert _parse_entry("## Long-term notes") is None

    def test_whitespace_stripped(self):
        result = _parse_entry("  - 2026-05-01 — [fact] content  ")
        assert result is not None
        assert result[1] == "fact"


# ── _is_stale_tag ──


class TestIsStaleTag:
    def test_stale(self):
        assert _is_stale_tag("intent?stale") is True

    def test_not_stale(self):
        assert _is_stale_tag("intent") is False
        assert _is_stale_tag("fact") is False

    def test_stale_suffix_only(self):
        assert _is_stale_tag("?stale") is True


# ── housekeep_memory — 核心整理逻辑 ──


class TestHousekeepMemory:
    """纯函数测试，无需文件 I/O。"""

    def _now(self, days_ago: int = 0) -> datetime:
        return datetime(2026, 6, 7, tzinfo=UTC) - timedelta(days=days_ago)

    def _entry(self, days_ago: int, category: str, content: str) -> str:
        date = self._now(days_ago).strftime("%Y-%m-%d")
        return f"- {date} — [{category}] {content}"

    # ── transient ──

    def test_transient_expired_removed(self):
        """PASS: transient > 7 天 → 删除。"""
        content = self._entry(8, "transient", "本周加班")
        new_content, removed, stale = housekeep_memory(content, now=self._now())
        assert removed == 1
        assert stale == 0
        assert new_content.strip() == ""

    def test_transient_within_window_kept(self):
        """PASS: transient ≤ 7 天 → 保留。"""
        content = self._entry(5, "transient", "这周赶项目")
        new_content, removed, stale = housekeep_memory(content, now=self._now())
        assert removed == 0
        assert "这周赶项目" in new_content

    def test_transient_exactly_7_days_kept(self):
        """BOUNDARY: transient 恰好 7 天 → 保留（> 7 才删）。"""
        content = self._entry(7, "transient", "刚好7天")
        new_content, removed, stale = housekeep_memory(content, now=self._now())
        assert removed == 0
        assert "刚好7天" in new_content

    # ── intent → stale ──

    def test_intent_marked_stale_after_30_days(self):
        """PASS: intent > 30 天 → 标记 ?stale。"""
        content = self._entry(31, "intent", "打算学 Rust")
        new_content, removed, stale = housekeep_memory(content, now=self._now())
        assert removed == 0
        assert stale == 1
        assert "[intent?stale]" in new_content

    def test_intent_within_30_days_kept(self):
        """PASS: intent ≤ 30 天 → 保留。"""
        content = self._entry(25, "intent", "打算学 Go")
        new_content, removed, stale = housekeep_memory(content, now=self._now())
        assert removed == 0
        assert stale == 0
        assert "[intent]" in new_content

    def test_intent_exactly_30_days_kept(self):
        """BOUNDARY: intent 恰好 30 天 → 保留（> 30 才标记）。"""
        content = self._entry(30, "intent", "30天整")
        new_content, removed, stale = housekeep_memory(content, now=self._now())
        assert stale == 0
        assert "30天整" in new_content

    # ── stale intent → delete ──

    def test_stale_intent_deleted_after_60_days(self):
        """PASS: intent?stale > 60 天（stale 超过 30*2）→ 删除。"""
        content = self._entry(61, "intent?stale", "过时的计划")
        new_content, removed, stale = housekeep_memory(content, now=self._now())
        assert removed == 1
        assert stale == 0
        assert new_content.strip() == ""

    def test_stale_intent_within_60_days_kept(self):
        """PASS: intent?stale ≤ 60 天 → 保留。"""
        content = self._entry(55, "intent?stale", "还没过期")
        new_content, removed, stale = housekeep_memory(content, now=self._now())
        assert removed == 0
        assert "还没过期" in new_content

    def test_stale_intent_exactly_60_days_kept(self):
        """BOUNDARY: intent?stale 恰好 60 天 → 保留（> 60 才删，因为 _INTENT_STALE_DAYS*2=60）。"""
        content = self._entry(60, "intent?stale", "60天整")
        new_content, removed, stale = housekeep_memory(content, now=self._now())
        assert removed == 0
        assert "60天整" in new_content

    # ── fact / preference / correction → 永不过期 ──

    def test_fact_never_expires(self):
        content = self._entry(365, "fact", "用户名字是小明")
        new_content, removed, stale = housekeep_memory(content, now=self._now())
        assert removed == 0
        assert stale == 0
        assert "小明" in new_content

    def test_preference_never_expires(self):
        content = self._entry(365, "preference", "喜欢深色主题")
        new_content, removed, stale = housekeep_memory(content, now=self._now())
        assert removed == 0
        assert "深色主题" in new_content

    def test_correction_never_expires(self):
        content = self._entry(365, "correction", "实际是小红不是小明")
        new_content, removed, stale = housekeep_memory(content, now=self._now())
        assert removed == 0
        assert "小红" in new_content

    # ── 边界情况 ──

    def test_empty_content(self):
        new_content, removed, stale = housekeep_memory("", now=self._now())
        assert removed == 0
        assert stale == 0
        assert new_content == ""

    def test_whitespace_only_content(self):
        new_content, removed, stale = housekeep_memory("   \n\n  ", now=self._now())
        assert removed == 0
        assert stale == 0

    def test_headers_preserved(self):
        """非条目行（标题、空行）原样保留。"""
        content = "# 关于你\n\n## Long-term notes\n\n"
        new_content, removed, stale = housekeep_memory(content, now=self._now())
        assert removed == 0
        assert "# 关于你" in new_content
        assert "## Long-term notes" in new_content

    def test_mixed_content(self):
        """混合各类条目 + 标题，正确分类处理。"""
        now = self._now()
        lines = [
            "# 关于你\n",
            "\n",
            "## Long-term notes\n",
            "\n",
            self._entry(100, "fact", "用户名小明") + "\n",
            self._entry(8, "transient", "本周加班") + "\n",
            self._entry(31, "intent", "打算学 Rust") + "\n",
            self._entry(2, "transient", "今天休息") + "\n",
        ]
        content = "".join(lines)

        new_content, removed, stale = housekeep_memory(content, now=now)
        assert removed == 1  # 8天前的 transient
        assert stale == 1  # 31天前的 intent
        assert "用户名小明" in new_content  # fact 保留
        assert "今天休息" in new_content  # 2天前 transient 保留
        assert "本周加班" not in new_content  # 8天前 transient 已删
        assert "[intent?stale]" in new_content  # intent 标记 stale
        assert "# 关于你" in new_content  # 标题保留

    def test_no_now_parameter_uses_current_time(self):
        """不传 now 参数时使用当前时间。"""
        content = "- 2020-01-01 — [transient] 很旧的条目\n"
        new_content, removed, stale = housekeep_memory(content)
        # 2020年到现在肯定超过7天
        assert removed == 1

    def test_invalid_date_format_kept(self):
        """日期格式无效的条目原样保留。"""
        content = "- not-a-date — [fact] 内容\n"
        new_content, removed, stale = housekeep_memory(content, now=self._now())
        assert removed == 0
        assert "not-a-date" in new_content


# ── MemoryHousekeeper 生命周期 ──


class TestMemoryHousekeeper:
    @pytest.mark.asyncio
    async def test_start_and_stop(self):
        """PASS: start/stop 生命周期不报错。"""
        hk = MemoryHousekeeper()
        hk.start()
        assert hk._running is True
        assert hk._task is not None
        await hk.stop()
        assert hk._running is False

    @pytest.mark.asyncio
    async def test_stop_without_start(self):
        """PASS: 未 start 直接 stop 不报错。"""
        hk = MemoryHousekeeper()
        await hk.stop()
        assert hk._running is False
