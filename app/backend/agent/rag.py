"""RAG 记忆检索 — 基于 LlamaIndex + Chroma + DashScope

用户的个人数据就是 RAG 的数据源：
- UserProfile：画像、目标、技能
- SkillRecord：技能成长时间线
- Project：项目经历
- Conversation：对话历史摘要

架构：
- 向量化：DashScopeEmbedding (text-embedding-v4)
- 向量库：ChromaVectorStore (PersistentClient)
- 切割：SentenceSplitter
- 索引：VectorStoreIndex
- 检索：as_retriever()
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any

import chromadb
from llama_index.core import Document as LlamaDocument
from llama_index.core import Settings, VectorStoreIndex
from llama_index.core.ingestion import IngestionPipeline
from llama_index.core.node_parser import SentenceSplitter
from llama_index.embeddings.dashscope import DashScopeEmbedding
from llama_index.vector_stores.chroma import ChromaVectorStore
from sqlalchemy.ext.asyncio import AsyncSession

from app.backend.config import USER_DATA_DIR, get_settings

logger = logging.getLogger(__name__)

# 配置
CHROMA_DIR = str(USER_DATA_DIR / "chroma_db")
COLLECTION = "career_os_memory"
CHUNK_SIZE = 600
CHUNK_OVERLAP = 50

# 全局单例（懒加载）
_retriever = None
_vector_store = None
_settings_done = False


def _ensure_settings() -> None:
    """配置 LlamaIndex 全局设置（仅首次调用）"""
    global _settings_done
    if _settings_done:
        return
    cfg = get_settings()
    Settings.embed_model = DashScopeEmbedding(
        model_name="text-embedding-v4",
        api_key=cfg.dashscope_api_key,
    )
    _settings_done = True


def _get_vector_store() -> ChromaVectorStore:
    """获取或创建 Chroma vector store"""
    global _vector_store
    if _vector_store is None:
        client = chromadb.PersistentClient(path=CHROMA_DIR)
        collection = client.get_or_create_collection(COLLECTION)
        _vector_store = ChromaVectorStore(chroma_collection=collection)
    return _vector_store


def _get_retriever():
    """获取检索器（懒加载）"""
    global _retriever
    _ensure_settings()
    if _retriever is None:
        vs = _get_vector_store()
        index = VectorStoreIndex.from_vector_store(vs)
        _retriever = index.as_retriever(similarity_top_k=5)
    return _retriever


def _refresh_retriever() -> None:
    """刷新检索器（索引变更后调用）"""
    global _retriever
    vs = _get_vector_store()
    index = VectorStoreIndex.from_vector_store(vs)
    _retriever = index.as_retriever(similarity_top_k=5)


def _clear_collection() -> None:
    """清空记忆 collection 并释放缓存"""
    global _retriever, _vector_store
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    with contextlib.suppress(Exception):
        client.delete_collection(COLLECTION)
    _vector_store = None
    _retriever = None


# ─── 用户数据 → LlamaIndex Document ────────────────────────


def _profile_to_docs(profile) -> list[LlamaDocument]:
    """将用户画像转为可索引的文档"""
    docs: list[LlamaDocument] = []
    parts: list[str] = []

    if profile.school_name:
        parts.append(f"学校：{profile.school_name}（{profile.school_level or ''}）")
    if profile.major:
        parts.append(f"专业：{profile.major}")
    if profile.grade:
        parts.append(f"年级：{profile.grade}")
    if profile.target_direction:
        parts.append(f"目标方向：{profile.target_direction}")
    if profile.target_company_level:
        parts.append(f"目标公司层级：{profile.target_company_level}")
    if profile.current_skills:
        skills_str = ", ".join(
            f"{s['name']}({s.get('level', '')})" if isinstance(s, dict) else str(s) for s in profile.current_skills
        )
        parts.append(f"当前技能：{skills_str}")
    if profile.graduation_year:
        parts.append(f"毕业年份：{profile.graduation_year}")

    if parts:
        docs.append(
            LlamaDocument(
                text="\n".join(parts),
                metadata={"type": "profile", "user_id": profile.user_id},
            )
        )
    return docs


def _skills_to_docs(skills: list) -> list[LlamaDocument]:
    """将技能记录转为可索引的文档"""
    docs: list[LlamaDocument] = []
    for skill in skills:
        parts = [f"技能：{skill.skill_name}"]
        parts.append(f"掌握程度：{skill.proficiency}")
        if skill.context:
            parts.append(f"来源：{skill.context}")
        parts.append(f"记录时间：{skill.created_at.strftime('%Y-%m-%d') if skill.created_at else '未知'}")
        docs.append(
            LlamaDocument(
                text="\n".join(parts),
                metadata={"type": "skill", "skill_name": skill.skill_name},
            )
        )
    return docs


def _projects_to_docs(projects: list) -> list[LlamaDocument]:
    """将项目经历转为可索引的文档"""
    docs: list[LlamaDocument] = []
    for proj in projects:
        parts = [f"项目：{proj.title}"]
        if proj.tech_stack:
            parts.append(f"技术栈：{proj.tech_stack}")
        if proj.role:
            parts.append(f"角色：{proj.role}")
        if proj.period:
            parts.append(f"时间：{proj.period}")
        if proj.description:
            parts.append(f"描述：{proj.description}")
        docs.append(
            LlamaDocument(
                text="\n".join(parts),
                metadata={"type": "project", "title": proj.title},
            )
        )
    return docs


def _conversations_to_docs(messages: list) -> list[LlamaDocument]:
    """将对话历史转为可索引的文档（最近的消息）"""
    docs: list[LlamaDocument] = []
    for msg in messages:
        if msg.role in ("user", "assistant") and msg.content:
            docs.append(
                LlamaDocument(
                    text=f"[{msg.role}] {msg.content[:500]}",
                    metadata={"type": "conversation", "role": msg.role},
                )
            )
    return docs


# ─── 用户记忆索引 ──────────────────────────────────────────


async def ingest_user_memory(db: AsyncSession, user_id: str) -> int:
    """从用户的个人数据构建记忆索引

    读取：画像 + 技能记录 + 项目经历 + 对话历史
    写入：Chroma 向量库（先清空旧数据，避免重复）

    Returns: 索引的文档数
    """
    _ensure_settings()

    # 清空旧用户记忆，避免重复索引
    _clear_collection()

    ldocs: list[LlamaDocument] = []

    # 1. 用户画像
    from sqlalchemy.future import select

    from app.backend.models.user import UserProfile

    result = await db.execute(select(UserProfile).where(UserProfile.user_id == user_id))
    profile = result.scalar_one_or_none()
    if profile:
        ldocs.extend(_profile_to_docs(profile))

    # 2. 技能记录
    from app.backend.models.skill_record import SkillRecord

    result = await db.execute(select(SkillRecord).where(SkillRecord.user_id == user_id))
    ldocs.extend(_skills_to_docs(result.scalars().all()))

    # 3. 项目经历
    from app.backend.models.project import Project

    result = await db.execute(select(Project).where(Project.user_id == user_id))
    ldocs.extend(_projects_to_docs(result.scalars().all()))

    # 4. 对话历史（最近 50 条，限定当前用户）
    from app.backend.models.conversation import Conversation, Message

    result = await db.execute(
        select(Message)
        .join(Conversation, Message.conversation_id == Conversation.conversation_id)
        .where(Conversation.user_id == user_id)
        .where(Message.role.in_(["user", "assistant"]))
        .order_by(Message.created_at.desc())
        .limit(50)
    )
    messages = list(result.scalars().all())
    ldocs.extend(_conversations_to_docs(messages))

    if not ldocs:
        logger.info("用户 %s 暂无个人数据可索引", user_id)
        return 0

    # Chunking + 向量化 + 写入 Chroma
    vs = _get_vector_store()
    pipeline = IngestionPipeline(
        transformations=[
            SentenceSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP),
        ],
        vector_store=vs,
    )
    pipeline.run(documents=ldocs)
    _refresh_retriever()

    logger.info("用户 %s 记忆索引完成：%d 条文档", user_id, len(ldocs))
    return len(ldocs)


# ─── 语义检索 ──────────────────────────────────────────────


async def search(query: str, top_k: int = 5) -> list[dict[str, Any]]:
    """语义检索 — 从用户记忆中返回 top_k 条匹配"""
    retriever = _get_retriever()
    retriever.similarity_top_k = top_k
    nodes = retriever.retrieve(query)

    return [
        {
            "content": node.text,
            "type": node.metadata.get("type", ""),
            "score": round(node.score or 0, 4),
        }
        for node in nodes
    ]


def reset_index() -> None:
    """清空记忆索引并重置检索器"""
    global _retriever, _vector_store
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    with contextlib.suppress(Exception):
        client.delete_collection(COLLECTION)
    _vector_store = None
    _retriever = None


# ─── 向后兼容 ─────────────────────────────────────────────


class SimpleRAG:
    """兼容适配器：对外暴露与旧版一致的 search() 接口"""

    def __init__(self) -> None:
        _ensure_settings()

    async def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        return await search(query, top_k)


def get_rag() -> SimpleRAG:
    return SimpleRAG()
