"""记忆写入层 — 事件写入、单条/批量记录。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TypedDict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lib.memory.models import GrowthEvent
from lib.memory.relational_store import GrowthEventRepository, _make_dedupe_key, _make_payload_hash
from shared.logging import get_logger

logger = get_logger(__name__)

# ── 语义去重配置 ──────────────────────────────────────────────────────────────
# 每种 event_type 对应的相似度阈值（all-MiniLM-L6-v2 cosine similarity）。
# 只有在此 dict 中的 event_type 才会触发 L2 语义去重。
# reflection_added: 故意排除（矛盾观察不应合并）
# contradiction_noted: 排除（必须保留每条独立观察）
# relationship_noted: 排除（关系在演化，不应合并）
_SEMANTIC_DEDUP_TYPES: dict[str, float] = {
    "significant_moment": 0.80,
    "decision_made": 0.82,
}


class EventSpec(TypedDict, total=False):
    event_type: str
    entity_type: str | None
    entity_id: str | None
    payload: dict | None
    source: str
    source_platform: str


def _deep_merge_payload(event_type: str, base: dict, update: dict) -> dict:
    """类型定制的 payload 合并策略。

    significant_moment: 列表字段累积（去重保序），标量字段新值优先。
    decision_made: 使用 newer content wins，context 字段合并。
    其他: 新值覆盖旧值（浅合并）。
    """
    if event_type == "significant_moment":
        result = base.copy()
        for k, v in update.items():
            if k in result and isinstance(result[k], list) and isinstance(v, list):
                # 去重保序：旧值在前，新值追加，整体去重
                seen: set = set()
                merged_list = []
                for item in result[k] + v:
                    key = json.dumps(item, sort_keys=True) if isinstance(item, dict) else item
                    if key not in seen:
                        seen.add(key)
                        merged_list.append(item)
                result[k] = merged_list
            else:
                result[k] = v  # 标量：新值优先
        return result

    if event_type == "decision_made":
        # content / decision 字段：新值优先
        # context / background / tags 等：合并
        result = base.copy()
        for k, v in update.items():
            if k in ("content", "decision", "outcome"):
                result[k] = v  # 新值覆盖
            elif k in result and isinstance(result[k], list) and isinstance(v, list):
                seen = set()
                merged_list = []
                for item in result[k] + v:
                    key = json.dumps(item, sort_keys=True) if isinstance(item, dict) else item
                    if key not in seen:
                        seen.add(key)
                        merged_list.append(item)
                result[k] = merged_list
            else:
                result[k] = v
        return result

    # 默认：新值覆盖旧值
    return {**base, **update}


async def _semantic_dedup_and_merge(
    spec: dict,
    user_id: str,
    db: AsyncSession,
) -> GrowthEvent | None:
    """L2 语义去重：在 LanceDB 中查找语义相似事件，找到则合并并返回已有事件。

    返回值：
    - GrowthEvent: 找到相似事件，已合并，调用方应跳过新事件写入
    - None: 未找到相似事件，正常写入流程继续

    异常处理：LanceDB 不可用或查询失败时，静默返回 None（降级为正常写入）。
    """
    from core.vector_store import NullProvider, get_document_index_provider

    provider = get_document_index_provider()
    if provider is None or isinstance(provider, NullProvider):
        return None

    event_type = spec["event_type"]
    threshold = _SEMANTIC_DEDUP_TYPES.get(event_type)
    if threshold is None:
        return None

    payload = spec.get("payload") or {}
    # 构建查询文本：优先 content/description/decision，回退到 JSON 序列化
    query_text = (
        payload.get("content")
        or payload.get("description")
        or payload.get("decision")
        or (json.dumps(payload, ensure_ascii=False) if payload else "")
    )
    if not query_text:
        return None

    try:
        hits = await provider.prefetch(query_text)
    except Exception:
        logger.warning("semantic_dedup.prefetch_failed", user_id=user_id, event_type=event_type)
        return None

    # 后过滤：仅保留同一 user_id + 同一 event_type 的命中
    filtered = [
        h
        for h in hits
        if h.metadata.get("user_id") == user_id and h.metadata.get("event_type") == event_type and h.score >= threshold
    ]
    if not filtered:
        return None

    # 取相似度最高的命中
    best = max(filtered, key=lambda h: h.score)

    # 从 doc_id "narrative:{event_id}" 解析出 event_id
    doc_id = best.doc_id
    if not doc_id.startswith("narrative:"):
        return None
    event_id = doc_id[len("narrative:") :]

    # 加载已有事件
    result = await db.execute(
        select(GrowthEvent).where(
            GrowthEvent.id == event_id,
            GrowthEvent.user_id == user_id,
        )
    )
    existing = result.scalar_one_or_none()
    if existing is None:
        return None

    # 合并 payload
    existing_payload: dict = {}
    if existing.payload_json:
        try:
            existing_payload = json.loads(existing.payload_json)
        except json.JSONDecodeError:
            existing_payload = {}

    merged_payload = _deep_merge_payload(event_type, existing_payload, payload)
    new_payload_hash = _make_payload_hash(merged_payload)
    new_dedupe_key = _make_dedupe_key(
        user_id,
        event_type,
        existing.entity_type,
        existing.entity_id,
        new_payload_hash,
    )

    # 记录被合并进来的 ID 列表
    absorbed: list[str] = []
    if existing.merged_from:
        try:
            absorbed = json.loads(existing.merged_from)
        except json.JSONDecodeError:
            absorbed = []

    # 生成一个临时 ID 代表本次即将被丢弃的传入事件（用于追踪）
    import uuid

    incoming_tentative_id = str(uuid.uuid4())
    absorbed.append(incoming_tentative_id)

    # 保存第一次合并前的原始 dedupe_key（只在首次合并时设置）
    if existing.original_dedupe_key is None:
        existing.original_dedupe_key = existing.dedupe_key

    # 更新已有事件
    existing.payload_json = json.dumps(merged_payload, ensure_ascii=False)
    existing.payload_hash = new_payload_hash
    existing.dedupe_key = new_dedupe_key
    existing.updated_at = datetime.now(UTC)
    existing.merged_from = json.dumps(absorbed, ensure_ascii=False)
    existing.projected_provider_at = None  # 触发补偿 worker 重新索引

    await db.flush()

    logger.info(
        "semantic_dedup.merged",
        user_id=user_id,
        event_type=event_type,
        existing_id=existing.id,
        score=best.score,
        threshold=threshold,
    )
    return existing


class MemoryWriter:
    """事件写入职责 — 纯写入，无 session 管理，无投影触发。

    db 必须显式传入。commit 和投影同步由 LumenMemory 编排。
    可独立实例化测试写入逻辑。
    """

    async def _write_events(
        self,
        user_id: str,
        events: list[dict] | list[EventSpec],
        db: AsyncSession,
    ) -> list[GrowthEvent]:
        """通用事件写入，仅 flush，不 commit。调用方负责 commit + projections。"""
        from core.config import get_settings

        settings = get_settings()

        repo = GrowthEventRepository(db)
        created: list[GrowthEvent] = []
        for spec in events:
            spec = spec  # type: ignore[assignment]
            event_type = spec["event_type"]  # type: ignore[typeddict-item]

            # ── L2 语义去重（功能开关 + 类型过滤）──
            if settings.semantic_dedup_enabled and event_type in _SEMANTIC_DEDUP_TYPES:
                merged = await _semantic_dedup_and_merge(dict(spec), user_id, db)
                if merged is not None:
                    continue  # 已被合并，跳过 L1 写入

            # ── L1 精确去重 ──
            event = await repo.create_with_dedup(
                user_id=user_id,
                event_type=event_type,
                entity_type=spec.get("entity_type"),
                entity_id=spec.get("entity_id"),
                payload=spec.get("payload"),
                source=spec.get("source", "system"),
                source_platform=spec.get("source_platform", "web"),
            )
            if event:
                created.append(event)
        if created:
            await db.flush()
        return created

    async def remember(
        self,
        user_id: str,
        event_type: str,
        entity_type: str | None = None,
        entity_id: str | None = None,
        payload: dict | None = None,
        source: str = "system",
        source_platform: str = "web",
        *,
        db: AsyncSession,
    ) -> GrowthEvent | None:
        """写入一条记忆事件（db 必须传入，仅 flush）。"""
        spec: EventSpec = {
            "event_type": event_type,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "payload": payload,
            "source": source,
            "source_platform": source_platform,
        }
        created = await self._write_events(user_id, [spec], db)
        return created[0] if created else None

    async def remember_batch(
        self,
        user_id: str,
        events: list[EventSpec],
        *,
        db: AsyncSession,
    ) -> list[GrowthEvent]:
        """批量写入（db 必须传入，仅 flush）。"""
        specs: list[dict] = [dict(e) for e in events]
        return await self._write_events(user_id, specs, db)
