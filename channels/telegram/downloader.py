"""Telegram 文件下载 + 暂存目录管理。

职责单一：将 Telegram Bot API 的 File 对象下载到本地暂存目录，
返回平台无关的 RawFile。
"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path

from lib.bus.queue import RawFile

logger = logging.getLogger(__name__)

# 暂存目录 — Channel 下载后由 AttachmentService 移入 session-files
_STAGING_DIR = Path.home() / ".lumen" / "tmp" / "telegram-incoming"

# Telegram Bot API 文件下载上限（20 MB）
_MAX_FILE_SIZE = 20 * 1024 * 1024


def _ensure_staging_dir() -> Path:
    _STAGING_DIR.mkdir(parents=True, exist_ok=True)
    return _STAGING_DIR


async def download_file(tg_file, original_name: str) -> RawFile | None:
    """下载 Telegram 文件到暂存目录。

    Args:
        tg_file: python-telegram-bot 的 File 对象（已 await xxx.get_file()）
        original_name: 原始文件名，用于 RawFile.original_name

    Returns:
        RawFile 或 None（下载失败时）
    """
    try:
        file_size = getattr(tg_file, "file_size", 0) or 0
        if file_size > _MAX_FILE_SIZE:
            logger.warning(
                "[telegram] 文件过大，跳过下载: %s, size=%d",
                original_name,
                file_size,
            )
            return None

        staging = _ensure_staging_dir()
        safe_name = f"{secrets.token_hex(6)}_{original_name}" if original_name else secrets.token_hex(6)
        dest = staging / safe_name

        await tg_file.download_to_drive(custom_path=str(dest))

        size = os.path.getsize(dest) if dest.exists() else 0
        mime = getattr(tg_file, "mime_type", None)

        return RawFile(
            path=str(dest),
            original_name=original_name,
            mime_type=mime,
            size=size,
        )
    except Exception as e:
        logger.warning("[telegram] 文件下载失败: %s, name=%s", e, original_name)
        return None
