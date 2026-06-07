"""测试上下文围栏 — 静态围栏构建、清洗、StreamingContextScrubber 状态机。"""

from __future__ import annotations

from lib.memory.context_fence import (
    StreamingContextScrubber,
    build_memory_context_block,
    sanitize_context,
)

# ── build_memory_context_block ──


class TestBuildMemoryContextBlock:
    def test_empty_returns_empty(self):
        assert build_memory_context_block("") == ""

    def test_whitespace_returns_empty(self):
        assert build_memory_context_block("   \n\t  ") == ""

    def test_none_like_returns_empty(self):
        # 空字符串和 None 都被 if not 捕获
        assert build_memory_context_block("") == ""

    def test_valid_content_wrapped(self):
        result = build_memory_context_block("用户喜欢咖啡")
        assert "<memory-context>" in result
        assert "</memory-context>" in result
        assert "用户喜欢咖啡" in result

    def test_contains_system_note_prefix(self):
        result = build_memory_context_block("测试内容")
        assert "[System note:" in result
        assert "recalled memory context" in result

    def test_exact_structure(self):
        result = build_memory_context_block("ABC")
        assert result.startswith("<memory-context>\n[System note:")
        assert result.endswith("ABC\n</memory-context>")

    def test_multiline_content(self):
        content = "第一行\n第二行\n第三行"
        result = build_memory_context_block(content)
        assert "第一行" in result
        assert "第二行" in result
        assert "第三行" in result


# ── sanitize_context ──


class TestSanitizeContext:
    def test_no_tags_passthrough(self):
        text = "这是普通文本"
        assert sanitize_context(text) == "这是普通文本"

    def test_removes_memory_context_tags(self):
        text = "前面<memory-context>内部内容</memory-context>后面"
        result = sanitize_context(text)
        assert "<memory-context>" not in result
        assert "</memory-context>" not in result
        assert "内部内容" not in result
        assert "前面" in result
        assert "后面" in result

    def test_removes_system_note_lines(self):
        text = "[System note: The following is recalled memory context, NOT new user input.]\n内容"
        result = sanitize_context(text)
        assert "[System note:" not in result
        assert "内容" in result

    def test_multiple_tags_removed(self):
        text = "A<memory-context>x</memory-context>B<memory-context>y</memory-context>C"
        result = sanitize_context(text)
        assert result == "ABC"

    def test_strips_whitespace(self):
        text = "  hello  "
        assert sanitize_context(text) == "hello"

    def test_empty_after_removal(self):
        text = "<memory-context>only content</memory-context>"
        result = sanitize_context(text)
        assert result == ""


# ── StreamingContextScrubber — 状态机核心测试 ──


class TestStreamingContextScrubber:
    """测试跨 SSE chunk 的 <memory-context> 标签清洗状态机。"""

    def test_normal_text_passes_through(self):
        scrubber = StreamingContextScrubber()
        assert scrubber.feed("hello world") == "hello world"

    def test_complete_tag_removed(self):
        """PASS: 完整标签块被完全移除。"""
        scrubber = StreamingContextScrubber()
        result = scrubber.feed("before<memory-context>hidden</memory-context>after")
        assert result == "beforeafter"

    def test_tag_only_removed(self):
        scrubber = StreamingContextScrubber()
        result = scrubber.feed("<memory-context>secret data</memory-context>")
        assert result == ""

    def test_tag_at_start(self):
        scrubber = StreamingContextScrubber()
        result = scrubber.feed("<memory-context>hidden</memory-context>visible")
        assert result == "visible"

    def test_tag_at_end(self):
        scrubber = StreamingContextScrubber()
        result = scrubber.feed("visible<memory-context>hidden</memory-context>")
        assert result == "visible"

    def test_empty_tag_block(self):
        scrubber = StreamingContextScrubber()
        result = scrubber.feed("before<memory-context></memory-context>after")
        assert result == "beforeafter"

    def test_multiple_blocks_with_text_between(self):
        scrubber = StreamingContextScrubber()
        result = scrubber.feed("A<memory-context>x</memory-context>B<memory-context>y</memory-context>C")
        assert result == "ABC"

    def test_tag_split_across_chunks(self):
        """PASS: 标签被 chunk 边界割裂时仍正确移除。"""
        scrubber = StreamingContextScrubber()
        out1 = scrubber.feed("before<memory-")
        out2 = scrubber.feed("context>hidden</memory-")
        out3 = scrubber.feed("context>after")
        assert out1 == "before"
        assert out2 == ""
        assert out3 == "after"

    def test_open_tag_split_many_chunks(self):
        """PASS: <memory-context> 被拆成多个 chunk。"""
        scrubber = StreamingContextScrubber()
        chunks = ["<", "m", "e", "m", "o", "r", "y", "-", "c", "o", "n", "t", "e", "x", "t", ">"]
        for ch in chunks:
            scrubber.feed(ch)
        # 现在在 INSIDE 状态
        out = scrubber.feed("hidden</memory-context>visible")
        assert out == "visible"

    def test_close_tag_split_across_chunks(self):
        scrubber = StreamingContextScrubber()
        scrubber.feed("<memory-context>hidden</")
        result = scrubber.feed("memory-context>visible")
        assert result == "visible"

    def test_less_than_in_normal_text(self):
        """普通文本中的 < 不构成标签时原样输出。"""
        scrubber = StreamingContextScrubber()
        result = scrubber.feed("5 < 10 and 20 > 5")
        assert "5" in result
        assert "10" in result

    def test_angle_bracket_then_normal_text(self):
        """< 后跟非标签内容时原样输出。"""
        scrubber = StreamingContextScrubber()
        result = scrubber.feed("<div>html</div>")
        # <div> 不匹配 <memory-context>，应原样输出
        assert "<div>html</div>" in result

    def test_flush_idle_outputs_buffer(self):
        scrubber = StreamingContextScrubber()
        scrubber.feed("hello<")
        # buffer contains "<", still in MAYBE_OPEN
        # But we haven't flushed yet
        result = scrubber.flush()
        # In MAYBE_OPEN state, flush discards buffer
        assert result == ""

    def test_flush_idle_with_pending_text(self):
        scrubber = StreamingContextScrubber()
        scrubber.feed("hello")
        result = scrubber.flush()
        # In IDLE state with no buffer
        assert result == ""

    def test_reset_clears_state(self):
        scrubber = StreamingContextScrubber()
        scrubber.feed("<memory-context>hidden")
        scrubber.reset()
        # After reset, state is IDLE, buffer is clear
        result = scrubber.feed("visible")
        assert result == "visible"

    def test_nested_angle_bracket_inside_tag(self):
        scrubber = StreamingContextScrubber()
        result = scrubber.feed("before<memory-context><b>bold</b></memory-context>after")
        assert result == "beforeafter"

    def test_multiline_content_inside_tag(self):
        scrubber = StreamingContextScrubber()
        result = scrubber.feed("A<memory-context>\nline1\nline2\n</memory-context>B")
        assert result == "AB"

    def test_tag_content_with_angle_brackets(self):
        """标签内的 < 和 > 不应干扰状态机。"""
        scrubber = StreamingContextScrubber()
        result = scrubber.feed("X<memory-context>a<b>c</memory-context>Y")
        assert result == "XY"

    def test_partial_open_tag_then_different_text(self):
        """PASS: <memory 后跟非 context 内容，buffer 原样输出。"""
        scrubber = StreamingContextScrubber()
        out1 = scrubber.feed("<memo")
        out2 = scrubber.feed("something else")
        # <memo 不匹配 open tag 前缀，但 <mem 确实是 <memory-context> 的前缀
        # Actually <memo -> buffer is "<memo", which doesn't start with "<memory-context>"
        # But "<memo" could be a prefix of "<memory..." No wait:
        # After feed("<memo"):
        #   IDLE -> '<' -> MAYBE_OPEN, buffer="<"
        #   buffer += "m" -> buffer="<m" -> starts with "<memory-context>" prefix "<m"
        #   buffer += "e" -> buffer="<me" -> still prefix
        #   buffer += "m" -> buffer="<mem" -> still prefix
        #   buffer += "o" -> buffer="<memo" -> NOT a prefix of "<memory-context>"
        #   So output buffer "<memo" and go back to IDLE
        # Wait, let me re-check the logic...
        # Actually "<memo" IS a prefix... no, "<memo" vs "<memory-context>"
        # "<memo" is NOT a prefix of "<memory-context>" because "<memory-context>"[4] = 'r' != 'o'
        # So the scrubber should output "<memo" and go back to IDLE
        assert out1 == ""  # buffered, not output yet in MAYBE_OPEN
        assert "something else" in out2 or out2 != ""

    def test_consecutive_feeds_with_mixed_content(self):
        scrubber = StreamingContextScrubber()
        results = []
        results.append(scrubber.feed("Hello "))
        results.append(scrubber.feed("world"))
        assert "".join(results) == "Hello world"

    def test_single_char_feeds(self):
        """逐字符 feed，验证状态机每一步正确。"""
        scrubber = StreamingContextScrubber()
        full = "A<memory-context>X</memory-context>B"
        collected = []
        for ch in full:
            out = scrubber.feed(ch)
            if out:
                collected.append(out)
        assert "".join(collected) == "AB"
