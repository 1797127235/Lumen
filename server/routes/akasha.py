"""Akasha 记忆引擎 Inspector 路由。"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException

from lib.memory.builtins.akasha.store import AkashaStore
from lib.memory.config_store import load_memory_provider_configs
from shared.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["akasha"])


def _get_akasha_store(user_id: str = "me") -> AkashaStore | None:
    """获取当前启用的 akasha provider 的 sidecar store。"""
    from lib.memory.builtins.akasha.config import resolve_akasha_db_path

    configs = load_memory_provider_configs()
    akasha_cfg = next(
        (c for c in configs if c.provider_type == "akasha" and c.enabled),
        None,
    )
    if akasha_cfg is None:
        return None

    from lib.memory.builtins.akasha.config import load_akasha_config

    cfg = load_akasha_config(akasha_cfg.config)
    db_path = resolve_akasha_db_path(user_id=user_id, akasha_config=cfg)
    return AkashaStore(db_path)


@router.get("/akasha/overview")
async def get_akasha_overview() -> dict[str, Any]:
    store = _get_akasha_store()
    if store is None:
        raise HTTPException(status_code=404, detail="Akasha provider 未启用")
    try:
        items, total = store.list_query_logs(page=1, page_size=1)
        latest = items[0]["ts"] if items else None
        return {"available": True, "total": total, "latest_at": latest}
    finally:
        store.close()


@router.get("/akasha/turns")
async def list_akasha_turns(
    session_key: str = "",
    q: str = "",
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    store = _get_akasha_store()
    if store is None:
        raise HTTPException(status_code=404, detail="Akasha provider 未启用")
    try:
        items, total = store.list_query_logs(
            session_key=session_key,
            q=q,
            page=page,
            page_size=page_size,
        )
        return {
            "items": items,
            "total": total,
            "page": max(1, page),
            "page_size": max(1, min(page_size, 200)),
        }
    finally:
        store.close()


@router.get("/akasha/turns/{query_id:path}")
async def get_akasha_turn(query_id: str) -> dict[str, Any]:
    store = _get_akasha_store()
    if store is None:
        raise HTTPException(status_code=404, detail="Akasha provider 未启用")
    try:
        raw = store.get_query_log(query_id)
        if raw is None:
            raise HTTPException(status_code=404, detail="Akasha 检索记录不存在")
        result = dict(raw)
        for json_key, out_key in [
            ("activation_items_json", "activation_items"),
            ("dense_items_json", "dense_items"),
            ("ripple_items_json", "ripple_items"),
        ]:
            raw_json = result.pop(json_key, "[]")
            try:
                parsed = json.loads(str(raw_json))
            except Exception:
                parsed = []
            result[out_key] = parsed if isinstance(parsed, list) else []
        return result
    finally:
        store.close()
