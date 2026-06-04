"""Session 附件副本管理 — 轻量复制、安全校验、生命周期清理。

基于 OpenHanako 的设计思路，但大幅简化：
- 无 SessionFileRegistry（文件系统即真相）
- 无独立 upload API（复制内联在 stream_chat 中）
- 仅支持单文件（不支持目录树）
"""

from __future__ import annotations

import asyncio
import os
import secrets
import shutil
import time
from pathlib import Path

from lib.tools._path_safety import is_read_denied
from shared.logging import get_logger

logger = get_logger(__name__)

MAX_ATTACHMENT_SIZE = 20 * 1024 * 1024
MAX_ATTACHMENTS = 5
MAX_FILENAME_BYTES = 255
SESSION_FILES_DIR = Path.home() / ".lumen" / "session-files"

_WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    "com1",
    "com2",
    "com3",
    "com4",
    "com5",
    "com6",
    "com7",
    "com8",
    "com9",
    "lpt1",
    "lpt2",
    "lpt3",
    "lpt4",
    "lpt5",
    "lpt6",
    "lpt7",
    "lpt8",
    "lpt9",
}

# ── 文件分类 ──
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


def _is_sensitive_path(file_path: str) -> bool:
    """使用统一的凭据集黑名单判断敏感路径。"""
    return is_read_denied(file_path) is not None


def _sanitize_filename(name: str) -> str:
    safe = Path(name).name
    # 替换控制字符和文件系统保留字符
    for cp in range(0x00, 0x20):
        safe = safe.replace(chr(cp), "_")
    for ch in '<>:"/\\|?*':
        safe = safe.replace(ch, "_")
    # 去除尾部空格和点（Windows 限制）
    safe = safe.rstrip(" .")
    if not safe:
        safe = "file"
    # 处理 Windows 保留设备名
    base = Path(safe).stem
    if base.lower() in _WINDOWS_RESERVED_NAMES:
        safe = f"file-{safe}"
    # UTF-8 字节截断到 255 字节
    encoded = safe.encode("utf-8")
    if len(encoded) > MAX_FILENAME_BYTES:
        truncated = encoded[:MAX_FILENAME_BYTES]
        while truncated:
            try:
                safe = truncated.decode("utf-8")
                break
            except UnicodeDecodeError:
                truncated = truncated[:-1]
    return safe


def _unique_name(base: str, ext: str) -> str:
    suffix = f"_{int(time.time())}_{secrets.token_hex(4)}"
    max_base = MAX_FILENAME_BYTES - len(suffix.encode("utf-8")) - len(ext.encode("utf-8"))
    if max_base < 1:
        max_base = 1
    truncated = base.encode("utf-8")[:max_base].decode("utf-8", errors="ignore")
    return f"{truncated}{suffix}{ext}"


def is_image(file_path: str) -> bool:
    return Path(file_path).suffix.lower() in _IMAGE_EXTENSIONS


async def _copy_attachments(conv_id: str, attachments: list[str]) -> list[str]:
    """复制附件到 session-files，返回复制后的路径列表。"""
    if len(attachments) > MAX_ATTACHMENTS:
        logger.warning("附件数量超限", count=len(attachments), max=MAX_ATTACHMENTS)
        attachments = attachments[:MAX_ATTACHMENTS]

    dest_dir = SESSION_FILES_DIR / conv_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    copied = []

    for src in attachments:
        try:
            if not os.path.isabs(src):
                logger.warning("附件路径必须是绝对路径", path=src)
                continue
            if os.path.islink(src):
                logger.warning("附件拒绝符号链接", path=src)
                continue
            if _is_sensitive_path(src):
                logger.warning("附件路径在敏感目录中", path=src)
                continue
            if os.path.isdir(src):
                logger.warning("附件不支持目录", path=src)
                continue
            size = os.path.getsize(src)
            if size > MAX_ATTACHMENT_SIZE:
                logger.warning("附件过大，已跳过", path=src, size=size)
                continue

            ext = Path(src).suffix
            base = Path(src).stem
            dest_name = _unique_name(_sanitize_filename(base), ext)
            dest = dest_dir / dest_name
            await asyncio.to_thread(shutil.copy2, src, dest)
            copied.append(str(dest))
        except OSError:
            logger.warning("附件复制失败", path=src)

    return copied


async def cleanup_session_files(conv_id: str) -> None:
    """删除指定会话的附件副本目录。"""
    dest_dir = SESSION_FILES_DIR / conv_id
    if dest_dir.exists():
        await asyncio.to_thread(shutil.rmtree, dest_dir, ignore_errors=True)


async def cleanup_orphan_session_files(db_session) -> None:
    """清理没有对应 conversation 的孤儿 session-files 目录。"""
    try:
        from sqlalchemy import select

        from lib.chat.models import Conversation

        result = await db_session.execute(select(Conversation.conversation_id))
        existing_ids = {str(r[0]) for r in result}

        def _scan_and_clean():
            for entry in SESSION_FILES_DIR.iterdir():
                if entry.is_dir() and entry.name not in existing_ids:
                    shutil.rmtree(entry, ignore_errors=True)
                    logger.info("清理孤儿 session-files", dir=entry.name)

        await asyncio.to_thread(_scan_and_clean)
    except Exception:
        logger.warning("清理孤儿 session-files 失败")
