"""本地文件系统连接器 — 扫描 .md / .txt 文件。"""

from __future__ import annotations

import asyncio
import mimetypes
import os
from collections.abc import AsyncIterator, Callable, Coroutine
from pathlib import Path

from backend.core.logging import get_logger
from backend.modules.data_sources.ingestion.connector import DataSourceConnector, RawBytes

logger = get_logger(__name__)

SUPPORTED_EXTENSIONS = {".md", ".txt", ".markdown", ".pdf", ".html", ".htm"}
MAX_FILE_SIZE_BYTES = 500 * 1024


def _guess_mime_type(path: Path) -> str | None:
    """猜测文件 mime-type，优先 python-magic，回退到 mimetypes 模块。"""
    try:
        import magic

        return magic.from_file(str(path), mime=True)
    except ImportError:
        mime, _ = mimetypes.guess_type(str(path))
        return mime


def _detect_charset(data: bytes) -> str:
    """检测文件编码，优先 charset-normalizer，回退到 utf-8。"""
    try:
        from charset_normalizer import detect

        result = detect(data)
        if result and result["encoding"]:
            return result["encoding"]
    except ImportError:
        pass
    return "utf-8"


class FilesystemConnector(DataSourceConnector):
    """扫描本地目录中的 Markdown / 文本文件。

    Phase 2 改造后只读取原始字节，不做任何解析或截断。
    """

    _source_id = "local_folder"

    def __init__(
        self,
        directories: list[str],
        *,
        user_id: str = "demo_user",
        data_source_id: str = "",
    ) -> None:
        self._dirs = [Path(d) for d in directories]
        self._user_id = user_id
        self._data_source_id = data_source_id or self._source_id
        self._observer = None  # watchdog Observer，延迟初始化

    @property
    def source_id(self) -> str:
        return self._source_id

    @property
    def data_source_id(self) -> str:
        return self._data_source_id

    def is_configured(self) -> bool:
        return any(d.is_dir() for d in self._dirs)

    def _is_hidden(self, path: Path) -> bool:
        """检查路径是否包含隐藏目录（以 . 开头）。"""
        return any(part.startswith(".") for part in path.parts)

    def _is_binary(self, data: bytes) -> bool:
        """简单启发式：检查是否包含 null byte 或控制字符（除换行/制表外）。"""
        if b"\x00" in data[:1024]:
            return True
        # 允许 UTF-8 多字节序列：检查无效 UTF-8 比例
        sample = data[:2048]
        try:
            sample.decode("utf-8")
            return False  # 能解码为 UTF-8 → 不是二进制
        except UnicodeDecodeError:
            pass
        # 回退：检查控制字符比例
        text_chars = set(range(32, 127)) | {9, 10, 13, 127}
        non_text = sum(1 for b in sample if b not in text_chars)
        return non_text / len(sample) > 0.30

    async def scan(self) -> AsyncIterator[RawBytes]:  # type: ignore[override]
        for directory in self._dirs:
            if not directory.is_dir():
                logger.warning("ingestion.filesystem.dir_not_found", path=str(directory))
                continue
            for file_path in directory.rglob("*"):
                # 跳过隐藏目录（.obsidian、.git 等）
                if self._is_hidden(file_path):
                    continue
                if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                    continue
                try:
                    stat = file_path.stat()
                except OSError:
                    continue
                doc = self._read_file(file_path, stat)
                if doc:
                    yield doc
                await asyncio.sleep(0)  # 让出事件循环，避免阻塞

    def _read_file(self, path: Path, stat: os.stat_result | None = None) -> RawBytes | None:
        try:
            # 大小检查
            file_size = stat.st_size if stat else path.stat().st_size
            if file_size > MAX_FILE_SIZE_BYTES:
                logger.debug("ingestion.filesystem.file_too_large", path=str(path), size=file_size)
                return None

            data = path.read_bytes()
            if not data:
                return None

            # 二进制文件过滤
            if self._is_binary(data):
                logger.debug("ingestion.filesystem.binary_skipped", path=str(path))
                return None

            mime_type = _guess_mime_type(path)
            mtime = stat.st_mtime if stat else path.stat().st_mtime
            resolved = path.resolve()

            return RawBytes(
                data_source_id=self._data_source_id,
                external_id=str(resolved),
                uri=resolved.as_uri(),
                content_bytes=data,
                mime_type=mime_type,
                metadata={
                    "extension": path.suffix,
                    "last_modified": mtime,
                    "size": file_size,
                },
                last_modified=mtime,
                user_id=self._user_id,
            )
        except Exception as exc:
            logger.warning("ingestion.filesystem.read_error", path=str(path), error=str(exc))
            return None

    def start_watching(
        self,
        on_change: Callable[[RawBytes], Coroutine],
        on_delete: Callable[[str, str], Coroutine],
        *,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        """启动 watchdog 文件监听。需要 `pip install watchdog`。"""
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            logger.warning("ingestion.filesystem.watchdog_not_installed")
            return

        connector = self
        DEBOUNCE_SECONDS = 1.5  # Obsidian 自动保存会连续触发多次，合并处理

        class _Handler(FileSystemEventHandler):
            def __init__(self) -> None:
                self._loop = loop
                self._timers: dict[str, asyncio.TimerHandle] = {}

            def _decode_path(self, path) -> str:
                """将 watchdog 路径（可能为 bytes）解码为 str。"""
                if isinstance(path, bytes):
                    return path.decode("utf-8", errors="replace")
                return path

            def _is_supported(self, path) -> bool:
                p = self._decode_path(path)
                return Path(p).suffix.lower() in SUPPORTED_EXTENSIONS

            def _schedule_change(self, src_path) -> None:
                """防抖：DEBOUNCE_SECONDS 内的重复事件只处理最后一次。"""
                path = self._decode_path(src_path)
                handle = self._timers.pop(path, None)
                if handle:
                    handle.cancel()

                def _fire():
                    self._timers.pop(path, None)
                    doc = connector._read_file(Path(path))
                    if doc:
                        asyncio.run_coroutine_threadsafe(on_change(doc), self._loop)

                self._timers[path] = loop.call_later(DEBOUNCE_SECONDS, _fire)

            def on_modified(self, event):  # type: ignore[override]
                if not event.is_directory and self._is_supported(event.src_path):
                    self._schedule_change(event.src_path)

            def on_created(self, event):  # type: ignore[override]
                if not event.is_directory and self._is_supported(event.src_path):
                    self._schedule_change(event.src_path)

            def on_deleted(self, event):  # type: ignore[override]
                if not event.is_directory and self._is_supported(event.src_path):
                    path = self._decode_path(event.src_path)
                    doc_id = str(Path(path).resolve())
                    asyncio.run_coroutine_threadsafe(on_delete(connector._data_source_id, doc_id), self._loop)

            def on_moved(self, event):  # type: ignore[override]
                # 重命名 = 旧路径删除 + 新路径新增
                if not event.is_directory:
                    if self._is_supported(event.src_path):
                        path = self._decode_path(event.src_path)
                        doc_id = str(Path(path).resolve())
                        asyncio.run_coroutine_threadsafe(on_delete(connector._data_source_id, doc_id), self._loop)
                    if self._is_supported(event.dest_path):
                        self._schedule_change(event.dest_path)

        self._observer = Observer()
        handler = _Handler()
        for directory in self._dirs:
            if directory.is_dir():
                self._observer.schedule(handler, str(directory), recursive=True)
        self._observer.start()
        logger.info("ingestion.filesystem.watching", dirs=[str(d) for d in self._dirs])

    def stop_watching(self) -> None:
        if self._observer:
            self._observer.stop()
            self._observer.join()
            self._observer = None
