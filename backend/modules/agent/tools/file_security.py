"""文件安全层 — 二进制检测、大小限制。

路径解析和越界检查已迁移到 PathPolicy（backend.agent.tools.core.policies），
由 Dispatcher 在调用 handler 之前统一处理。
"""

from __future__ import annotations

from pathlib import Path

# =============================================================================
# 二进制文件检测
# =============================================================================

# 常见二进制扩展名
_BINARY_EXTENSIONS = frozenset(
    {
        # 可执行文件
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".bin",
        # 图片
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".bmp",
        ".ico",
        ".svg",
        # 音视频
        ".mp3",
        ".mp4",
        ".wav",
        ".avi",
        ".mov",
        ".mkv",
        ".flv",
        # 压缩
        ".zip",
        ".tar",
        ".gz",
        ".bz2",
        ".7z",
        ".rar",
        ".xz",
        # 文档
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
        # 其他二进制
        ".db",
        ".sqlite",
        ".o",
        ".a",
        ".class",
        ".pyc",
        ".pyo",
    }
)

# 图片扩展名（可返回 base64）
_IMAGE_EXTENSIONS = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".bmp",
        ".ico",
    }
)


def is_binary_file(path: Path, content_sample: str | bytes | None = None) -> tuple[bool, bool]:
    """检查文件是否为二进制文件。

    Returns:
        (是否二进制, 是否图片)
    """
    ext = path.suffix.lower()

    # 扩展名检查（快速路径）
    if ext in _BINARY_EXTENSIONS:
        return True, ext in _IMAGE_EXTENSIONS

    # 内容采样检查（后备）
    if content_sample:
        if isinstance(content_sample, bytes):
            sample = content_sample[:1000]
            non_printable = sum(1 for b in sample if b < 32 and b not in (9, 10, 13))
            ratio = non_printable / max(len(sample), 1)
        else:
            sample = content_sample[:1000]
            non_printable = sum(1 for c in sample if ord(c) < 32 and c not in "\n\r\t")
            ratio = non_printable / max(len(sample), 1)
        return ratio > 0.30, False

    return False, False


# =============================================================================
# 大小限制
# =============================================================================

# 单次读取最大字符数（约 25-35K tokens）
DEFAULT_MAX_READ_CHARS = 100_000

# 大文件提示阈值
_LARGE_FILE_HINT_BYTES = 512_000


def check_size_limits(file_size: int, content_length: int, max_chars: int = DEFAULT_MAX_READ_CHARS) -> str | None:
    """检查文件大小是否超过限制。

    Returns:
        错误消息（如果超限），None（如果通过）
    """
    if content_length > max_chars:
        return (
            f"读取内容 {content_length:,} 字符超过安全限制 ({max_chars:,} 字符). "
            "请使用 offset 和 limit 参数读取特定范围."
        )
    return None
