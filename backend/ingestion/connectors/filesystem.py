"""本地文件系统连接器 — 扫描 .md / .txt 文件。"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Callable, Coroutine
from pathlib import Path

from backend.ingestion.connector import DataSourceConnector, RawDocument
from backend.logging_config import get_logger

logger = get_logger(__name__)

SUPPORTED_EXTENSIONS = {".md", ".txt", ".markdown"}
MAX_FILE_SIZE_BYTES = 500 * 1024
# 摄入时内容截断上限（字符数）
MAX_CONTENT_CHARS = 50000


class FilesystemConnector(DataSourceConnector):
    """扫描本地目录中的 Markdown / 文本文件。"""

    _source_id = "filesystem"

    def __init__(self, directories: list[str]) -> None:
        self._dirs = [Path(d) for d in directories]
        self._observer = None  # watchdog Observer，延迟初始化

    @property
    def source_id(self) -> str:
        return self._source_id

    def is_configured(self) -> bool:
        return any(d.is_dir() for d in self._dirs)

    def _is_hidden(self, path: Path) -> bool:
        """检查路径是否包含隐藏目录（以 . 开头）。"""
        return any(part.startswith(".") for part in path.parts)

    async def scan(self) -> AsyncIterator[RawDocument]:
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

    def _read_file(self, path: Path, stat: os.stat_result | None = None) -> RawDocument | None:
        try:
            content = path.read_text(encoding="utf-8", errors="ignore").strip()
            if not content:
                return None
            # 超大文件截断（字符数）
            if len(content) > MAX_CONTENT_CHARS:
                logger.debug("ingestion.filesystem.file_truncated", path=str(path), original_len=len(content))
                content = content[:MAX_CONTENT_CHARS]
            mtime = stat.st_mtime if stat else path.stat().st_mtime
            return RawDocument(
                source_id=self.source_id,
                doc_id=str(path.resolve()),
                content=content,
                metadata={
                    "title": path.stem,
                    "extension": path.suffix,
                    "last_modified": mtime,
                },
            )
        except Exception as exc:
            logger.warning("ingestion.filesystem.read_error", path=str(path), error=str(exc))
            return None

    def start_watching(
        self,
        on_change: Callable[[RawDocument], Coroutine],
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

            def _is_supported(self, path: str) -> bool:
                return Path(path).suffix.lower() in SUPPORTED_EXTENSIONS

            def _schedule_change(self, src_path: str) -> None:
                """防抖：DEBOUNCE_SECONDS 内的重复事件只处理最后一次。"""
                handle = self._timers.pop(src_path, None)
                if handle:
                    handle.cancel()

                def _fire():
                    self._timers.pop(src_path, None)
                    doc = connector._read_file(Path(src_path))
                    if doc:
                        asyncio.run_coroutine_threadsafe(on_change(doc), self._loop)

                self._timers[src_path] = loop.call_later(DEBOUNCE_SECONDS, _fire)

            def on_modified(self, event):  # type: ignore[override]
                if not event.is_directory and self._is_supported(event.src_path):
                    self._schedule_change(event.src_path)

            def on_created(self, event):  # type: ignore[override]
                if not event.is_directory and self._is_supported(event.src_path):
                    self._schedule_change(event.src_path)

            def on_deleted(self, event):  # type: ignore[override]
                if not event.is_directory and self._is_supported(event.src_path):
                    doc_id = str(Path(event.src_path).resolve())
                    asyncio.run_coroutine_threadsafe(on_delete(connector.source_id, doc_id), self._loop)

            def on_moved(self, event):  # type: ignore[override]
                # 重命名 = 旧路径删除 + 新路径新增
                if not event.is_directory:
                    if self._is_supported(event.src_path):
                        doc_id = str(Path(event.src_path).resolve())
                        asyncio.run_coroutine_threadsafe(on_delete(connector.source_id, doc_id), self._loop)
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
