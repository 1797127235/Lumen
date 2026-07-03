"""Markdown 原子读写 + 安全扫描 — 文件优先记忆存储。

保留旧代码兼容的 sync 函数（read_memory / read_about_you / write_about_you），
新增 AsyncMarkdownStore 供新架构使用。
"""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.config import USER_DATA_DIR
from shared.logging import get_logger

logger = get_logger(__name__)

# ── 路径常量 ──

_BASE_MEMORY_DIR = USER_DATA_DIR / "memory"


# ═══════════════════════════════════════════════════════════
# 旧代码兼容层（sync 函数）
# ═══════════════════════════════════════════════════════════


def memory_dir(user_id: str) -> Path:
    safe_id = Path(user_id).name
    return _BASE_MEMORY_DIR / safe_id


def ensure_memory_dirs(user_id: str) -> None:
    memory_dir(user_id).mkdir(parents=True, exist_ok=True)


def read_memory(user_id: str) -> str:
    memory_file = memory_dir(user_id) / "MEMORY.md"
    if not memory_file.exists():
        return ""
    return memory_file.read_text(encoding="utf-8")


def read_about_you(user_id: str) -> str:
    path = memory_dir(user_id) / "USER.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


_META_RE = re.compile(r"^<!-- lumen-meta:.*?-->\n?")


def _strip_meta(content: str) -> str:
    """剥离 about_you.md 的元数据注释行，返回纯内容。"""
    return _META_RE.sub("", content, count=1)


# ── YAML frontmatter 解析 ──

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """解析 Markdown 的 YAML frontmatter。

    Returns:
        (frontmatter_dict, body_without_frontmatter)
        frontmatter_dict 为空 dict 表示没有 frontmatter。
    """
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return {}, content

    yaml_text = match.group(1)
    body = content[match.end() :]

    # 简单 YAML 解析（只支持顶层 key: value，不处理嵌套）
    data: dict[str, Any] = {}
    for line in yaml_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, val = line.split(":", 1)
            key = key.strip()
            val = val.strip()
            # 尝试解析 list
            if val.startswith("[") and val.endswith("]"):
                val = [v.strip().strip("\"'") for v in val[1:-1].split(",") if v.strip()]
            elif val in ("true", "True"):
                val = True
            elif val in ("false", "False"):
                val = False
            elif val in ("null", "Null", "~"):
                val = None
            data[key] = val

    return data, body


def _dump_frontmatter(data: dict[str, Any]) -> str:
    """将 dict 序列化为 YAML frontmatter 字符串（不含分隔线）。"""
    lines: list[str] = []
    for key, val in data.items():
        if val is None:
            lines.append(f"{key}: null")
        elif isinstance(val, bool):
            lines.append(f"{key}: {'true' if val else 'false'}")
        elif isinstance(val, list):
            items = ", ".join(f'"{v}"' for v in val)
            lines.append(f"{key}: [{items}]")
        else:
            lines.append(f"{key}: {val}")
    return "\n".join(lines)


def _truncate_to_limit(content: str, limit: int) -> str:
    if len(content) <= limit:
        return content
    truncated = content[:limit]
    last_newline = truncated.rfind("\n\n")
    if last_newline > 0:
        truncated = truncated[:last_newline]
    logger.warning("Content truncated", orig=len(content), truncated=len(truncated))
    return truncated


def _write_md_file_safe(path: str, content: str, max_chars: int | None = None) -> None:
    """原子写入 Markdown 文件（sync 版本）。"""
    if max_chars is not None:
        content = _truncate_to_limit(content, max_chars)
    dir_name = os.path.dirname(path)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=dir_name, suffix=".tmp", delete=False) as handle:
        handle.write(content)
        temp_path = handle.name
    os.replace(temp_path, path)


def write_about_you(user_id: str, content: str, *, event_count: int = 0) -> None:
    """写入 AI 综合画像（sync 兼容函数）。"""
    ensure_memory_dirs(user_id)
    now = datetime.now(UTC).isoformat()
    meta = f"<!-- lumen-meta: events={event_count} generated_at={now} -->\n"
    _write_md_file_safe(
        str(memory_dir(user_id) / "USER.md"),
        meta + content,
        max_chars=1375,
    )


# ═══════════════════════════════════════════════════════════
# 新架构：AsyncMarkdownStore
# ═══════════════════════════════════════════════════════════

_MD_CHAR_LIMITS: dict[str, int] = {
    "memory": 2200,
    "about_you": 1375,
    "partner": 800,
}

# Prompt injection / role hijack 检测模式
_INJECTION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"ignore\s+(?:all\s+)?.*?previous\s+instructions", re.IGNORECASE), "忽略先前指令"),
    (re.compile(r"ignore\s+(?:all\s+)?.*?(?:above|prior)\s+instructions", re.IGNORECASE), "忽略上文指令"),
    (
        re.compile(r"you\s+are\s+now\s+(?:instructed|required|ordered|commanded|told)\s+to", re.IGNORECASE),
        "角色劫持（you are now instructed to）",
    ),
    (
        re.compile(
            r"you\s+are\s+now\s+(?:an?|the)\s+(?:attacker|hacker|spy|adversary|intruder|evil|malicious|unfiltered|unrestricted|jailbreak|DAN)",
            re.IGNORECASE,
        ),
        "角色劫持（恶意角色）",
    ),
    (
        re.compile(r"system\s*:\s*(?:new|updated|ignore|disregard|override|you\s+are|from\s+now\s+on)", re.IGNORECASE),
        "伪造 system 消息",
    ),
    (re.compile(r"\[system\s+note\]", re.IGNORECASE), "伪造 system note"),
    (re.compile(r"new\s+instructions\s*:", re.IGNORECASE), "注入新指令"),
    (re.compile(r"disregard\s+.*?instructions", re.IGNORECASE), "要求忽略指令"),
    (re.compile(r"forget\s+.*(?:instructions|rules|prompt)", re.IGNORECASE), "要求遗忘指令"),
    (re.compile(r"from\s+now\s+on\s+you\s+are", re.IGNORECASE), "角色切换"),
    (re.compile(r"pretend\s+to\s+be", re.IGNORECASE), "要求伪装角色"),
    (re.compile(r"act\s+as\s+if\s+you\s+are", re.IGNORECASE), "要求扮演角色"),
]

# 数据外泄 / 凭据提取检测模式
_DATA_EXFIL_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"curl\s+.*(?:token|key|secret|password|api[_-]?key|credential)", re.IGNORECASE), "curl 外泄凭据"),
    (re.compile(r"wget\s+.*(?:token|key|secret|password|api[_-]?key|credential)", re.IGNORECASE), "wget 外泄凭据"),
    (
        re.compile(r"python\s+.*requests\.(?:get|post)\s*\(.*(?:token|key|secret|password|api)", re.IGNORECASE),
        "Python requests 外泄凭据",
    ),
    (re.compile(r"fetch\s*\(\s*['\"].*(?:token|key|secret|password|api)", re.IGNORECASE), "fetch 外泄凭据"),
    (re.compile(r"\.env\b.*(?:token|key|secret|password|credential|api)", re.IGNORECASE), "请求 .env 文件敏感内容"),
    (re.compile(r"sk-[a-zA-Z0-9]{20,}"), "OpenAI-style API key"),
    (re.compile(r"Bearer\s+[a-zA-Z0-9_\-]{20,}", re.IGNORECASE), "Bearer token"),
    (re.compile(r"base64\s*\(\s*[^)]+(?:token|key|secret|password|api)", re.IGNORECASE), "base64 编码外泄凭据"),
]

# 隐形 Unicode 字符
_INVISIBLE_UNICODE = re.compile(r"[\u200B\u200C\u200D\uFEFF\u202A-\u202E\u2066-\u2069]")


def _scan_memory_content(content: str) -> tuple[bool, str]:
    """安全扫描：检测 prompt injection、数据外泄、隐形 Unicode。

    返回 (is_safe, reason)。is_safe=True 表示通过扫描。
    """
    for pat, label in _INJECTION_PATTERNS:
        if pat.search(content):
            return False, f"检测到 prompt injection / 角色劫持模式: {label}"
    for pat, label in _DATA_EXFIL_PATTERNS:
        if pat.search(content):
            return False, f"检测到潜在数据外泄 / 凭据提取: {label}"
    if _INVISIBLE_UNICODE.search(content):
        return False, "检测到隐形 Unicode 字符（可能被用于 prompt injection）"
    return True, ""


# 跨进程文件锁（Windows: msvcrt / Unix: fcntl）


def _acquire_file_lock(lock_path: Path) -> int:
    """获取文件锁，返回文件描述符。"""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        import msvcrt

        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        return fd
    else:
        import fcntl

        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd


def _release_file_lock(fd: int, lock_path: Path) -> None:
    """释放文件锁。"""
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


class AsyncMarkdownStore:
    """异步 Markdown 文件存储 — 原子写入 + 安全扫描 + 并发锁。"""

    def __init__(self) -> None:
        # 每个 user_id 一个 asyncio.Lock，进程内串行化并发写入
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def _user_dir(self, user_id: str) -> Path:
        safe_id = Path(user_id).name
        return _BASE_MEMORY_DIR / safe_id

    def _ensure_dirs(self, user_id: str) -> None:
        self._user_dir(user_id).mkdir(parents=True, exist_ok=True)

    def _memory_path(self, user_id: str) -> Path:
        return self._user_dir(user_id) / "MEMORY.md"

    def _about_you_path(self, user_id: str) -> Path:
        return self._user_dir(user_id) / "USER.md"

    def _partner_path(self, user_id: str) -> Path:
        return self._user_dir(user_id) / "PARTNER.md"

    def _lock_path(self, user_id: str) -> Path:
        return self._user_dir(user_id) / ".lock"

    # ── 底层读写 ──

    async def _read(self, path: Path) -> str:
        if not path.exists():
            return ""
        return await asyncio.to_thread(path.read_text, encoding="utf-8")

    async def _write_atomic(self, path: Path, content: str) -> None:
        """原子写入：临时文件 + rename。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        loop = asyncio.get_event_loop()

        def _sync_write() -> None:
            fd, temp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
            try:
                os.write(fd, content.encode("utf-8"))
            finally:
                os.close(fd)
            os.replace(temp_path, path)

        await loop.run_in_executor(None, _sync_write)

    # ── 公开接口 ──

    async def read_memory(self, user_id: str) -> str:
        return await self._read(self._memory_path(user_id))

    async def write_memory(self, user_id: str, content: str) -> None:
        """覆写 memory.md（整文件写入）。"""
        safe, reason = _scan_memory_content(content)
        if not safe:
            logger.warning("MEMORY.md 写入被拒绝", user_id=user_id, reason=reason)
            return

        async with self._locks[user_id]:
            lock_fd = await asyncio.to_thread(_acquire_file_lock, self._lock_path(user_id))
            try:
                await self._write_atomic(self._memory_path(user_id), content)
            finally:
                await asyncio.to_thread(_release_file_lock, lock_fd, self._lock_path(user_id))

    async def append_memory_entry(self, user_id: str, category: str, text: str) -> None:
        """追加一条记忆条目到 MEMORY.md 的 ## Long-term notes 下。

        格式：- 日期 — [category] 内容
        category 可以是: fact, preference, intent, transient, correction,
        或自定义标签（向后兼容旧调用方）。
        """
        safe, reason = _scan_memory_content(text)
        if not safe:
            logger.warning("MEMORY.md 追加被拒绝", user_id=user_id, reason=reason)
            return

        date_str = datetime.now(UTC).strftime("%Y-%m-%d")
        entry = f"- {date_str} — [{category}] {text}\n"

        async with self._locks[user_id]:
            lock_fd = await asyncio.to_thread(_acquire_file_lock, self._lock_path(user_id))
            try:
                existing = await self._read(self._memory_path(user_id))
                if not existing.strip():
                    existing = "# 关于你\n\n## Long-term notes\n\n"

                # 确保有 ## Long-term notes 章节
                if "## Long-term notes" not in existing:
                    existing += "\n\n## Long-term notes\n\n"

                new_content = existing + entry

                # 大小限制检查
                limit = _MD_CHAR_LIMITS["memory"]
                if len(new_content) > limit:
                    # 丢弃最旧条目直到符合限制
                    lines = new_content.splitlines(keepends=True)
                    first_entry_idx = next(
                        (i for i, line in enumerate(lines) if line.strip().startswith("- ")),
                        -1,
                    )
                    if first_entry_idx >= 0:
                        while len("".join(lines)) > limit and first_entry_idx < len(lines):
                            # 找到下一条条目的起始位置（若无则删到末尾）
                            next_entry_idx = next(
                                (
                                    i
                                    for i in range(first_entry_idx + 1, len(lines))
                                    if lines[i].strip().startswith("- ")
                                ),
                                len(lines),
                            )
                            lines = lines[:first_entry_idx] + lines[next_entry_idx:]
                        new_content = "".join(lines)
                    else:
                        new_content = _truncate_to_limit(new_content, limit)
                    logger.warning("MEMORY.md 超限时丢弃旧条目", user_id=user_id, limit=limit)

                await self._write_atomic(self._memory_path(user_id), new_content)
            finally:
                await asyncio.to_thread(_release_file_lock, lock_fd, self._lock_path(user_id))

    async def read_about_you(self, user_id: str) -> str:
        return await self._read(self._about_you_path(user_id))

    async def write_about_you(self, user_id: str, content: str) -> None:
        """覆写 USER.md。"""
        safe, reason = _scan_memory_content(content)
        if not safe:
            logger.warning("USER.md 写入被拒绝", user_id=user_id, reason=reason)
            return

        async with self._locks[user_id]:
            lock_fd = await asyncio.to_thread(_acquire_file_lock, self._lock_path(user_id))
            try:
                limit = _MD_CHAR_LIMITS["about_you"]
                if len(content) > limit:
                    content = _truncate_to_limit(content, limit)
                await self._write_atomic(self._about_you_path(user_id), content)
            finally:
                await asyncio.to_thread(_release_file_lock, lock_fd, self._lock_path(user_id))

    async def load_frozen_snapshot(self, user_id: str) -> str:
        """读取 MEMORY.md + USER.md，同时注入作为 L0 冻结快照。

        Store 本身只按 user_id 读文件；按 conversation 冻结/缓存在更上层。
        任一文件缺失或为空时跳过，不阻塞另一个文件的注入。
        """
        parts: list[str] = []

        memory = await self.read_memory(user_id)
        if memory.strip():
            parts.append(memory)

        user = await self.read_about_you(user_id)
        if user.strip():
            parts.append(_strip_meta(user))

        return "\n\n".join(parts)

    async def read_partner(self, user_id: str) -> str:
        """读取 PARTNER.md 内容。"""
        return await self._read(self._partner_path(user_id))

    async def write_partner(self, user_id: str, content: str) -> None:
        """覆写 PARTNER.md（原子写入 + 安全扫描 + 字符限制）。"""
        safe, reason = _scan_memory_content(content)
        if not safe:
            logger.warning("PARTNER.md 写入被拒绝", user_id=user_id, reason=reason)
            return

        async with self._locks[user_id]:
            lock_fd = await asyncio.to_thread(_acquire_file_lock, self._lock_path(user_id))
            try:
                limit = _MD_CHAR_LIMITS["partner"]
                if len(content) > limit:
                    content = _truncate_to_limit(content, limit)
                    logger.warning("PARTNER.md 超限截断", user_id=user_id, limit=limit)
                await self._write_atomic(self._partner_path(user_id), content)
            finally:
                await asyncio.to_thread(_release_file_lock, lock_fd, self._lock_path(user_id))

    async def append_partner_rule(self, user_id: str, rule: str) -> None:
        """追加一条协作规则到 PARTNER.md。

        格式：- 规则内容
        """
        safe, reason = _scan_memory_content(rule)
        if not safe:
            logger.warning("PARTNER.md 规则追加被拒绝", user_id=user_id, reason=reason)
            return

        async with self._locks[user_id]:
            lock_fd = await asyncio.to_thread(_acquire_file_lock, self._lock_path(user_id))
            try:
                existing = await self._read(self._partner_path(user_id))
                if not existing.strip():
                    existing = "# 协作方式\n\n"

                if "# 协作方式" not in existing:
                    existing += "\n\n# 协作方式\n\n"

                entry = f"- {rule}\n"
                new_content = existing + entry

                limit = _MD_CHAR_LIMITS["partner"]
                if len(new_content) > limit:
                    lines = new_content.splitlines(keepends=True)
                    header_end = 0
                    in_entries = False
                    first_entry_idx = -1
                    for i, line in enumerate(lines):
                        if line.strip().startswith("- ") and not in_entries:
                            in_entries = True
                            first_entry_idx = i
                        if in_entries and i > first_entry_idx and line.strip().startswith("- "):
                            header_end = i
                            break
                    if header_end > 0:
                        new_content = "".join(lines[: first_entry_idx + 1] + lines[header_end:])
                    else:
                        new_content = _truncate_to_limit(new_content, limit)
                    logger.warning("PARTNER.md 超限时丢弃旧规则", user_id=user_id, limit=limit)

                await self._write_atomic(self._partner_path(user_id), new_content)
            finally:
                await asyncio.to_thread(_release_file_lock, lock_fd, self._lock_path(user_id))

    async def reset_user_memory(self, user_id: str) -> None:
        """清空用户的记忆文件。"""
        async with self._locks[user_id]:
            lock_fd = await asyncio.to_thread(_acquire_file_lock, self._lock_path(user_id))
            try:
                await self._write_atomic(self._memory_path(user_id), "")
                await self._write_atomic(self._about_you_path(user_id), "")
                await self._write_atomic(self._partner_path(user_id), "")
            finally:
                await asyncio.to_thread(_release_file_lock, lock_fd, self._lock_path(user_id))


# ═══════════════════════════════════════════════════════════
# 旧代码兼容 stub（阶段 5 删除旧代码后可移除）
# ═══════════════════════════════════════════════════════════


async def sync_user_md_projection(user_id: str, *, db=None) -> bool:
    """兼容层：新架构下不再需要事件投影，直接返回成功。"""
    import warnings

    warnings.warn(
        "sync_user_md_projection is deprecated (Hermes-Pure)",
        DeprecationWarning,
        stacklevel=2,
    )
    logger.debug("sync_user_md_projection 兼容 stub 被调用", user_id=user_id)
    return True


async def project_user_to_md(db, user_id: str) -> bool:
    """兼容层：新架构下不再需要事件投影，直接返回成功。"""
    import warnings

    warnings.warn(
        "project_user_to_md is deprecated (Hermes-Pure)",
        DeprecationWarning,
        stacklevel=2,
    )
    logger.debug("project_user_to_md 兼容 stub 被调用", user_id=user_id)
    return True
