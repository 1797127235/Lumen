"""知识库应用服务层 — 文件处理业务逻辑，无 HTTP 依赖"""

from __future__ import annotations

from pydantic_ai import Agent

from backend.agent.deps import LumenDeps
from backend.agent.pydantic_agent import _create_model
from backend.agent.tools.tool_memory_save import register as register_memory_save
from backend.agent.tools.tool_profile import register as register_profile
from backend.config import get_settings
from backend.db import get_async_session_maker
from backend.domain.models import UploadedFile
from backend.domain.schemas import FilePayload
from backend.logging_config import get_logger
from backend.memory.chunker import chunk_text
from backend.memory.datasets import DATASET_REFERENCE
from backend.memory.facade import get_memory
from backend.memory.semantic_store import SemanticStore
from backend.utils.parsers import parse_file

logger = get_logger(__name__)


def _smart_truncate(text: str, max_chars: int = 5000) -> str:
    """按段落边界截断文本，避免断在句子/单词中间。"""
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    last_para = max(truncated.rfind("\n\n"), truncated.rfind("\n"))
    if last_para > max_chars * 0.8:
        return truncated[:last_para]
    return truncated


_EXTRACT_PROMPT = "阅读以下用户上传的文件内容，提取对用户画像有价值的信息。\n\n" "【文件：{filename}】\n{text}"


def _get_extract_agent() -> Agent[LumenDeps, str]:
    """创建轻量提取 Agent（不缓存，避免配置漂移）。"""
    agent = Agent(
        model=_create_model(),
        deps_type=LumenDeps,
        output_type=str,
        system_prompt=(
            "你是一个文档信息提取助手。阅读用户上传的文档内容，判断其中是否包含"
            "对用户个人画像有价值的信息。\n\n"
            "你有这些工具：\n"
            "- update_profile：更新用户基本信息（学校、专业、目标等）\n"
            "- memory_save：保存技能、经历、偏好、目标、决策等\n\n"
            "规则：\n"
            "1. 只提取和用户本人相关的信息（学过什么、做过什么、想要什么）\n"
            "2. 参考资料、技术文档、第三方内容不提取\n"
            "3. 如果没有值得提取的信息，回复「无需提取」，不要编造\n"
            "4. 提取时保持原文意思，不要过度总结\n"
            "5. 调用工具后不需要生成额外文字，直接结束"
        ),
        retries=2,
    )

    register_memory_save(agent)
    register_profile(agent)
    return agent


async def extract_and_save(user_id: str, text: str, filename: str) -> None:
    """调轻量 Agent 审查文件内容，有价值的信息写入记忆层。"""
    settings = get_settings()
    if not (settings.llm_api_key or settings.dashscope_api_key):
        logger.debug("Skipping extraction: no API key configured")
        return

    agent = _get_extract_agent()
    prompt = _EXTRACT_PROMPT.format(filename=filename, text=_smart_truncate(text))

    async with get_async_session_maker()() as db:
        deps = LumenDeps(
            user_id=user_id,
            db=db,
            conversation_id=None,
            current_user_input=prompt,
        )
        await agent.run(prompt, deps=deps)
        await db.commit()

        if deps.pending_event_ids:
            await get_memory().sync_projections(user_id, deps.pending_event_ids)
            logger.info(
                "Agent extracted %d events from file",
                len(deps.pending_event_ids),
                filename=filename,
            )


async def process_file(
    record_id: str,
    user_id: str,
    filename: str,
    file_type: str,
    content: bytes,
    file_hash: str,
    storage_path: str,
) -> None:
    """后台异步处理：解析 → 分块 → 事件写入 → 语义索引 → Agent 提取。"""
    async with get_async_session_maker()() as db:
        record = await db.get(UploadedFile, record_id)
        if not record:
            logger.warning("UploadedFile record not found", record_id=record_id)
            return

        try:
            record.status = "processing"
            await db.commit()

            result = await parse_file(filename, content)
            text = result.text
            if not text.strip():
                record.status = "failed"
                record.error_message = "文件内容为空或无法提取文本"
                await db.commit()
                return

            chunks = chunk_text(text)
            preview = text[:200].replace("\n", " ")

            payload = FilePayload(
                filename=filename,
                file_type=file_type,
                file_hash=file_hash,
                size_bytes=len(content),
                storage_path=storage_path,
                chunk_count=len(chunks),
                preview=preview,
                metadata=result.metadata,
            ).model_dump()

            memory = get_memory()
            event = await memory.remember(
                user_id=user_id,
                event_type="document_uploaded",
                entity_type="document",
                entity_id=file_hash[:16],
                payload=payload,
                source="user_upload",
            )

            if event:
                semantic = SemanticStore()
                for i, chunk in enumerate(chunks):
                    doc_id = f"{event.id}_chunk_{i}"
                    indexed_content = f"[文件: {filename}] [块 {i + 1}/{len(chunks)}]\n{chunk}"
                    await semantic.ingest(
                        content=indexed_content,
                        doc_id=doc_id,
                        dataset=DATASET_REFERENCE,
                    )
                await memory.sync_projections(user_id, event_ids=[event.id])

            # Agent 内容提取
            try:
                await extract_and_save(user_id=user_id, text=text, filename=filename)
            except Exception as exc:
                logger.warning("Agent extraction failed, skipping", filename=filename, error=str(exc))

            record = await db.get(UploadedFile, record_id)
            if record:
                record.status = "ready"
                record.chunk_count = len(chunks)
                record.preview = preview
                record.event_id = str(event.id) if event else None
                await db.commit()

            logger.info("File processed", filename=filename, chunks=len(chunks), status="ready")
        except Exception as exc:
            logger.warning("File processing failed", filename=filename, error=str(exc))
            record = await db.get(UploadedFile, record_id)
            if record:
                record.status = "failed"
                record.error_message = str(exc)[:500]
                await db.commit()
