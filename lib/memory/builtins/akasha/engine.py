"""akasha 记忆引擎 — Lumen 适配运行时。

去掉旧 agent-framework 依赖，直接封装 core/store/replay 算法，
提供 AkashaEngine.query() / commit_turn() 两个主要入口。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from lib.llm.embeddings import AsyncEmbeddingClient

from .config import AkashaConfig, load_akasha_config, resolve_akasha_db_path
from .core import (
    AkashaActivationSnapshot,
    AkashaCandidate,
    AkashaNode,
    CoreConfig,
    SourceMessage,
    activation_edge_updates,
    activation_updates,
    build_dense_message_index,
    compute_candidates_from_snapshot,
    edges_by_src,
    fan_counts,
    graph_seed_keys_from_snapshot,
    parse_turn_key,
    turn_key,
)
from .store import AkashaStore


@dataclass
class AkashaQueryResult:
    """Akasha 检索结果。"""

    text: str = ""
    cards: list[dict[str, Any]] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)


def _core_config(akasha_config: AkashaConfig) -> CoreConfig:
    return CoreConfig(
        dense_top_k=akasha_config.dense_top_k,
        dense_seed_threshold=akasha_config.dense_seed_threshold,
        activation_threshold=akasha_config.activation_threshold,
        cross_boost=akasha_config.cross_boost,
        nearby_time_seconds=akasha_config.nearby_time_seconds,
        nearby_dense_threshold=akasha_config.nearby_dense_threshold,
        soft_recall_threshold=akasha_config.soft_recall_threshold,
        soft_recall_direct_floor=akasha_config.soft_recall_direct_floor,
        activate_limit=akasha_config.activate_limit,
    )


def _format_context_block(cards: list[dict[str, Any]], *, now_ts: float) -> str:
    """把卡片格式化为注入上下文的 Markdown 文本。"""
    if not cards:
        return ""
    lines: list[str] = ["## Akasha 记忆召回"]
    for card in cards:
        score = card.get("score", 0.0)
        lane = card.get("lane", "unknown")
        user = card.get("user_message", "")
        assistant = card.get("assistant_preview", "")
        happened = card.get("happened_at", "")
        lines.append(f"- [{lane}] 相关度 {score:.3f} ({happened})")
        if user:
            lines.append(f"  用户：{user[:200]}")
        if assistant:
            lines.append(f"  AI：{assistant[:200]}")
    return "\n".join(lines)


class AkashaEngine:
    """Akasha 图记忆引擎的 Lumen 适配封装。"""

    def __init__(
        self,
        user_id: str,
        config: dict[str, Any] | None = None,
        embedder: AsyncEmbeddingClient | None = None,
    ) -> None:
        self._user_id = user_id
        self._akasha_config = load_akasha_config(config)
        self._db_path = resolve_akasha_db_path(
            user_id=user_id,
            akasha_config=self._akasha_config,
        )
        self._store = AkashaStore(self._db_path)
        self._embedder = embedder
        self._lock = threading.RLock()
        self._nodes: dict[str, AkashaNode] = {}
        self._message_embeddings: dict[str, np.ndarray] = {}
        self._message_turn_keys: dict[str, str] = {}
        self._message_index = build_dense_message_index({})
        self._load_graph_cache()

    @property
    def db_path(self) -> Path:
        return self._db_path

    def close(self) -> None:
        self._store.close()

    # ── 图缓存 ──

    def _load_graph_cache(self) -> None:
        """加载节点、边、embedding 缓存到内存。"""
        nodes = self._store.list_nodes()
        _edges, _ = self._store.load_edges_with_meta()
        with self._lock:
            self._nodes = {node.key: node for node in nodes}
            self._message_embeddings = {}
            self._message_turn_keys = {}
            self._message_index = build_dense_message_index({})

    def _graph_snapshot(self) -> AkashaActivationSnapshot:
        """构建当前图快照。"""
        edges, edges_meta = self._store.load_edges_with_meta()
        with self._lock:
            return AkashaActivationSnapshot(
                nodes=dict(self._nodes),
                edges=edges,
                edges_meta=edges_meta,
                fan=fan_counts(edges),
                edges_by_src=edges_by_src(edges),
                message_embeddings=dict(self._message_embeddings),
                message_turn_keys=dict(self._message_turn_keys),
            )

    # ── 检索 ──

    async def query(self, session_key: str, query_text: str) -> AkashaQueryResult:
        """根据 query_text 检索历史记忆。"""
        query_text = query_text.strip()
        if not query_text:
            return AkashaQueryResult()

        if self._embedder is None:
            return AkashaQueryResult()

        now_ts = datetime.now(UTC).timestamp()
        query_vec = np.array(await self._embedder.embed_one(query_text), dtype=np.float32)

        snapshot = self._graph_snapshot()
        cfg = _core_config(self._akasha_config)

        graph_seed_keys = graph_seed_keys_from_snapshot(
            query_vec,
            snapshot,
            limit=self._akasha_config.dense_top_k,
        )

        candidates, ripple_items, trace = compute_candidates_from_snapshot(
            query_text,
            query_vec,
            snapshot,
            now_ts,
            config=cfg,
            source_cursor=None,
            soft_recall=False,
            return_limit=self._akasha_config.activate_limit,
            graph_seed_keys=graph_seed_keys,
        )

        # 构造卡片
        cards = await self._cards_from_candidates(candidates[: self._akasha_config.activate_limit])
        text = _format_context_block(cards, now_ts=now_ts)

        # 记录 query log
        self._store.insert_query_log(
            query_id=f"q-{int(now_ts * 1000)}",
            session_key=session_key,
            seq=0,
            query_text=query_text,
            intent="context",
            ts=datetime.now(UTC).isoformat(),
            seed_count=trace.seed_count,
            pool_count=trace.pool_count,
            activated_count=len(candidates),
            activation_threshold=self._akasha_config.activation_threshold,
            dense_count=len(candidates),
            ripple_count=len(ripple_items),
            inject_chars=len(text),
            source_ref_count=len(cards),
            activation_items_json="[]",
            dense_items_json="[]",
            ripple_items_json="[]",
            text_block_preview=text[:200],
        )

        return AkashaQueryResult(
            text=text,
            cards=cards,
            trace={
                "engine": "akasha",
                "dense_count": len(candidates),
                "ripple_count": len(ripple_items),
                "seed_count": trace.seed_count,
                "pool_count": trace.pool_count,
            },
        )

    async def _cards_from_candidates(self, candidates: list[AkashaCandidate]) -> list[dict[str, Any]]:
        """把候选节点转换成可读卡片。"""
        cards: list[dict[str, Any]] = []
        for item in candidates:
            node = self._nodes.get(item.key)
            if node is None:
                continue
            parsed = parse_turn_key(item.key)
            session = parsed[0] if parsed else ""
            seq = parsed[1] if parsed else 0
            user_message, assistant_preview = self._store.get_turn_content(item.key)
            cards.append(
                {
                    "key": item.key,
                    "session_key": session,
                    "seq": seq,
                    "score": item.score,
                    "lane": item.source,
                    "happened_at": datetime.fromtimestamp(node.first_ts_unix, UTC).isoformat(),
                    "user_message": user_message,
                    "assistant_preview": assistant_preview,
                }
            )
        return cards

    # ── 写入 ──

    async def commit_turn(
        self,
        session_key: str,
        user_msg: str,
        assistant_msg: str,
        user_msg_id: str,
        assistant_msg_id: str,
        seq: int,
    ) -> None:
        """把一轮对话写入 Akasha 图。"""
        if self._embedder is None:
            return

        now_iso = datetime.now(UTC).isoformat()
        now_ts = datetime.now(UTC).timestamp()

        # embed user + assistant
        embeddings = await self._embedder.embed([user_msg, assistant_msg])
        user_emb, assistant_emb = embeddings[0], embeddings[1]

        # 在写入当前 turn 前先对历史图做一次激活，用于生成共激活边
        current_turn_key = turn_key(session_key, seq, "user")[2]
        snapshot = self._graph_snapshot()
        prior_nodes = dict(snapshot.nodes)
        prior_nodes.pop(current_turn_key, None)
        prior_edges = {
            (src, dst): w
            for (src, dst), w in snapshot.edges.items()
            if src != current_turn_key and dst != current_turn_key
        }
        prior_fan = fan_counts(prior_edges)
        prior_edges_by_src = edges_by_src(prior_edges)
        prior_snapshot = AkashaActivationSnapshot(
            nodes=prior_nodes,
            edges=prior_edges,
            edges_meta=snapshot.edges_meta,
            fan=prior_fan,
            edges_by_src=prior_edges_by_src,
            message_embeddings=dict(snapshot.message_embeddings),
            message_turn_keys=dict(snapshot.message_turn_keys),
            message_index=snapshot.message_index,
        )

        activation_candidates: list[AkashaCandidate] = []
        if prior_nodes:
            query_vec = np.array(user_emb, dtype=np.float32)
            graph_seed_keys = graph_seed_keys_from_snapshot(
                query_vec,
                prior_snapshot,
                limit=self._akasha_config.dense_top_k,
            )
            activation_candidates, _, _ = compute_candidates_from_snapshot(
                user_msg,
                query_vec,
                prior_snapshot,
                now_ts,
                config=_core_config(self._akasha_config),
                source_cursor=None,
                soft_recall=False,
                return_limit=self._akasha_config.activate_limit,
                graph_seed_keys=graph_seed_keys,
            )
            # 过滤掉当前 turn（理论上不会命中，但防御性处理）
            activation_candidates = [c for c in activation_candidates if c.key != current_turn_key]

        user_key = self._upsert_message(
            SourceMessage(
                id=user_msg_id,
                session_key=session_key,
                seq=seq,
                role="user",
                content=user_msg,
                ts=now_iso,
            ),
            user_emb,
        )
        assistant_key = self._upsert_message(
            SourceMessage(
                id=assistant_msg_id,
                session_key=session_key,
                seq=seq,
                role="assistant",
                content=assistant_msg,
                ts=now_iso,
            ),
            assistant_emb,
        )

        # 保存原文用于召回展示
        preview_limit = self._akasha_config.assistant_preview_chars
        assistant_preview = (
            assistant_msg if len(assistant_msg) <= preview_limit else assistant_msg[:preview_limit] + "..."
        )
        self._store.upsert_turn_content(user_key, user_msg, assistant_preview)

        with self._lock:
            self._message_embeddings[user_msg_id] = np.array(user_emb, dtype=np.float32)
            self._message_embeddings[assistant_msg_id] = np.array(assistant_emb, dtype=np.float32)
            self._message_turn_keys[user_msg_id] = user_key
            self._message_turn_keys[assistant_msg_id] = assistant_key
            self._message_index = build_dense_message_index(self._message_embeddings)

        # 写入共激活边并更新被激活旧节点状态
        if activation_candidates and user_key:
            edge_updates = activation_edge_updates(user_key, activation_candidates, now_ts)
            self._store.upsert_edges(edge_updates)
            self._store.update_activation_batch(activation_updates(activation_candidates, prior_nodes, now_ts))
            # 刷新本地缓存以反映边和节点状态变化
            self._load_graph_cache()

    def _upsert_message(self, message: SourceMessage, embedding: list[float]) -> str:
        """写入/更新单个消息节点。"""
        key = self._store.upsert_message_node(message, embedding)
        with self._lock:
            node = self._store.get_node(key)
            if node is not None:
                self._nodes[key] = node
        return key
