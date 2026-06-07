"""测试 AI 综合画像生成 — 防抖逻辑、输出解析、重入保护。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from lib.memory.understanding import (
    _DEBOUNCE_SECONDS,
    _LAST_UPDATE,
    _PENDING_TASKS,
    _is_debounced,
    _parse_understanding_output,
)


class TestIsDebounced:
    """测试 _is_debounced 防抖逻辑。"""

    def _setup_state(self, user_id: str, last_update: datetime | None = None, task: asyncio.Task | None = None):
        """设置全局状态用于测试。"""
        if last_update is not None:
            _LAST_UPDATE[user_id] = last_update
        else:
            _LAST_UPDATE.pop(user_id, None)

        if task is not None:
            _PENDING_TASKS[user_id] = task
        else:
            _PENDING_TASKS.pop(user_id, None)

    def _cleanup(self, user_id: str):
        _LAST_UPDATE.pop(user_id, None)
        _PENDING_TASKS.pop(user_id, None)

    def test_no_history_not_debounced(self):
        """无历史记录 → 不防抖。"""
        uid = "test_debounce_1"
        self._cleanup(uid)
        assert _is_debounced(uid) is False

    def test_recent_update_is_debounced(self):
        """PASS: 最近更新过（5 分钟内）→ 防抖。"""
        uid = "test_debounce_2"
        self._setup_state(uid, last_update=datetime.now(UTC))
        try:
            assert _is_debounced(uid) is True
        finally:
            self._cleanup(uid)

    def test_old_update_not_debounced(self):
        """超过 5 分钟 → 不防抖。"""
        uid = "test_debounce_3"
        self._setup_state(uid, last_update=datetime.now(UTC) - timedelta(seconds=_DEBOUNCE_SECONDS + 1))
        try:
            assert _is_debounced(uid) is False
        finally:
            self._cleanup(uid)

    def test_exactly_5_minutes_not_debounced(self):
        """BOUNDARY: 恰好 5 分钟 → 不防抖（< 5分钟才防抖）。"""
        uid = "test_debounce_4"
        self._setup_state(uid, last_update=datetime.now(UTC) - timedelta(seconds=_DEBOUNCE_SECONDS))
        try:
            assert _is_debounced(uid) is False
        finally:
            self._cleanup(uid)

    def test_just_under_5_minutes_is_debounced(self):
        """BOUNDARY: 4分59秒 → 防抖。"""
        uid = "test_debounce_5"
        self._setup_state(uid, last_update=datetime.now(UTC) - timedelta(seconds=_DEBOUNCE_SECONDS - 1))
        try:
            assert _is_debounced(uid) is True
        finally:
            self._cleanup(uid)

    @pytest.mark.asyncio
    async def test_pending_task_is_debounced(self):
        """PASS: 有进行中的 task → 防抖（重入保护）。"""
        uid = "test_debounce_6"

        async def slow_task():
            await asyncio.sleep(10)

        task = asyncio.create_task(slow_task())
        self._setup_state(uid, task=task)
        try:
            assert _is_debounced(uid) is True
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            self._cleanup(uid)

    @pytest.mark.asyncio
    async def test_completed_task_not_debounced(self):
        """已完成的 task → 不防抖。"""
        uid = "test_debounce_7"

        async def done_task():
            pass

        task = asyncio.create_task(done_task())
        await task  # 等待完成
        self._setup_state(uid, task=task)
        try:
            assert _is_debounced(uid) is False
        finally:
            self._cleanup(uid)


class TestParseUnderstandingOutput:
    """测试 LLM 输出解析 — 画像文本与模式洞察分离。"""

    def test_no_patterns_separator(self):
        text = "你是一位热爱编程的人。"
        about, patterns = _parse_understanding_output(text)
        assert about == "你是一位热爱编程的人。"
        assert patterns == []

    def test_with_patterns(self):
        raw = '你是一位热爱编程的人。\n\n---PATTERNS---\n[{"insight": "测试", "category": "test", "evidence_count": 1}]'
        about, patterns = _parse_understanding_output(raw)
        assert "热爱编程" in about
        assert len(patterns) == 1
        assert patterns[0]["insight"] == "测试"

    def test_invalid_json_returns_empty_patterns(self):
        raw = "画像文本\n\n---PATTERNS---\nnot valid json"
        about, patterns = _parse_understanding_output(raw)
        assert "画像文本" in about
        assert patterns == []

    def test_multiple_patterns(self):
        patterns_json = '[{"insight": "a", "category": "test", "evidence_count": 1}, {"insight": "b", "category": "test", "evidence_count": 2}]'
        raw = f"画像\n\n---PATTERNS---\n{patterns_json}"
        _, patterns = _parse_understanding_output(raw)
        assert len(patterns) == 2

    def test_non_list_json_returns_empty(self):
        raw = '画像\n\n---PATTERNS---\n{"key": "value"}'
        _, patterns = _parse_understanding_output(raw)
        assert patterns == []

    def test_empty_string(self):
        about, patterns = _parse_understanding_output("")
        assert about == ""
        assert patterns == []

    def test_whitespace_stripped(self):
        raw = "  画像文本  \n\n---PATTERNS---\n[]"
        about, _ = _parse_understanding_output(raw)
        assert about == "画像文本"  # stripped


# 用于 suppress CancelledError
import contextlib
