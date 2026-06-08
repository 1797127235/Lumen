"""文件附件统一处理 — 校验、存储、类型识别、内容描述。

横切服务，供 AgentRunner 调用，与具体 Channel 解耦。
Channel 只负责从平台下载文件到暂存目录并通过 RawFile 传递；
本服务负责将暂存文件安全地纳入 session-files 生命周期管理，
并为 Agent 生成可操作的文件描述。
"""

from __future__ import annotations

import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path

from lib.bus.queue import RawFile
from lib.chat.session_files import (
    MAX_ATTACHMENT_SIZE,
    SESSION_FILES_DIR,
    _is_sensitive_path,
    _sanitize_filename,
    _unique_name,
)
from shared.logging import get_logger

logger = get_logger(__name__)

# ── 文件分类 ──

_DOCUMENT_EXTS = {
    ".pdf",
    ".docx",
    ".pptx",
    ".xlsx",
    ".odt",
    ".ods",
    ".odp",
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".xml",
    ".tex",
    ".rtf",
}

_AUDIO_EXTS = {".ogg", ".mp3", ".wav", ".m4a", ".flac", ".aac"}
_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}


# ── 数据结构 ──


@dataclass
class MediaAttachment:
    """处理后的附件 — 包含最终存储路径和元数据。"""

    file_path: str
    original_name: str
    media_type: str  # "document" | "image" | "audio" | "video"
    mime_type: str | None
    size: int


# ── 服务 ──


def _classify(ext: str) -> str:
    """根据扩展名判断 media_type。"""
    ext = ext.lower()
    if ext in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
        return "image"
    if ext in _DOCUMENT_EXTS:
        return "document"
    if ext in _AUDIO_EXTS:
        return "audio"
    if ext in _VIDEO_EXTS:
        return "video"
    return "document"  # 未知类型按文档处理，Agent 可用 file_read 尝试


def _detect_mime(path: str, name: str) -> str | None:
    """MIME 类型检测：优先用扩展名，回退到 mimetypes。"""
    mime, _ = mimetypes.guess_type(name)
    if mime:
        return mime
    # 尝试用文件路径
    mime, _ = mimetypes.guess_type(path)
    return mime


class AttachmentService:
    """文件附件统一处理服务。

    职责：
    1. 接收 Channel 产出的 RawFile 列表（暂存目录中的文件）
    2. 校验（大小、敏感路径）
    3. 移入 session-files/{conv_id}/
    4. 识别类型并生成 MediaAttachment
    5. 为 Agent 生成可操作的文件描述
    """

    def __init__(self, max_size: int = MAX_ATTACHMENT_SIZE) -> None:
        self._max_size = max_size

    async def process(
        self,
        conv_id: str,
        raw_files: list[RawFile],
    ) -> list[MediaAttachment]:
        """将暂存文件处理为 session-files 中的正式附件。

        Args:
            conv_id: 会话 ID，决定存储子目录。
            raw_files: Channel 提取的原始文件列表。

        Returns:
            处理后的 MediaAttachment 列表（跳过校验失败的文件）。
        """
        if not raw_files:
            return []

        dest_dir = SESSION_FILES_DIR / conv_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        results: list[MediaAttachment] = []

        for raw in raw_files:
            try:
                attachment = await self._process_single(raw, dest_dir)
                if attachment:
                    results.append(attachment)
            except Exception:
                logger.warning(
                    "附件处理失败",
                    path=raw.path,
                    name=raw.original_name,
                )

        return results

    async def _process_single(
        self,
        raw: RawFile,
        dest_dir: Path,
    ) -> MediaAttachment | None:
        """处理单个文件：校验 → 移动 → 识别。"""
        src = raw.path

        # 校验：路径存在
        if not os.path.exists(src):
            logger.warning("附件不存在", path=src)
            return None

        # 校验：大小
        size = os.path.getsize(src)
        if size > self._max_size:
            logger.warning(
                "附件过大",
                path=src,
                size=size,
                max=self._max_size,
            )
            return None

        # 校验：敏感路径
        if _is_sensitive_path(src):
            logger.warning("附件路径在敏感目录中", path=src)
            return None

        # 校验：符号链接
        if os.path.islink(src):
            logger.warning("附件拒绝符号链接", path=src)
            return None

        # 生成安全的文件名并移动
        ext = os.path.splitext(raw.original_name or src)[1]
        base = os.path.splitext(raw.original_name or os.path.basename(src))[0]
        safe_name = _unique_name(_sanitize_filename(base), ext)
        dest = dest_dir / safe_name

        import asyncio
        import shutil

        await asyncio.to_thread(shutil.move, src, dest)

        # 类型识别
        final_ext = ext.lower()
        media_type = _classify(final_ext)
        mime = raw.mime_type or _detect_mime(str(dest), raw.original_name)

        return MediaAttachment(
            file_path=str(dest),
            original_name=raw.original_name or os.path.basename(src),
            media_type=media_type,
            mime_type=mime,
            size=size,
        )

    def build_content_hint(self, attachments: list[MediaAttachment]) -> str:
        """为 Agent 生成文件描述，拼接到 user_input 前。

        根据文件类型给出不同的引导提示，让 Agent 知道如何处理。
        """
        if not attachments:
            return ""

        lines = ["<attached-files>"]

        _TYPE_LABELS = {"image": "图片", "audio": "音频", "video": "视频"}
        for att in attachments:
            label = _TYPE_LABELS.get(att.media_type, "文件")
            lines.append(
                f"- [{label}] {att.original_name} ({att.mime_type or att.media_type}, "
                f"{self._fmt_size(att.size)}) — 已保存到 {att.file_path}"
            )

        lines.append("</attached-files>")
        return "\n".join(lines)

    @staticmethod
    def _fmt_size(size: int) -> str:
        if size < 1024:
            return f"{size}B"
        if size < 1024 * 1024:
            return f"{size / 1024:.1f}KB"
        return f"{size / 1024 / 1024:.1f}MB"


# ── 全局单例 ──

_attachment_service: AttachmentService | None = None


def get_attachment_service() -> AttachmentService:
    """获取全局 AttachmentService 单例。"""
    global _attachment_service
    if _attachment_service is None:
        _attachment_service = AttachmentService()
    return _attachment_service
