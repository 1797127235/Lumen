"""记忆管理 API — 文件优先架构（Hermes-Pure）。

改造后：
- MEMORY.md / USER.md 直接读写
- 退役路由返回空结果（前端兼容）
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from lib.agent.system_prompt_builder import invalidate_system_prompt_cache
from lib.memory.markdown import AsyncMarkdownStore, ensure_memory_dirs, memory_dir
from shared.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/memory", tags=["memory"])
_store = AsyncMarkdownStore()

_USER_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _validate_user_id(user_id: str) -> str:
    if not _USER_ID_PATTERN.match(user_id):
        raise HTTPException(
            status_code=400,
            detail="user_id 格式无效，只允许字母、数字、下划线和连字符，长度 1-64",
        )
    return user_id


# ═══════════════════════════════════════════
#  数据模型
# ═══════════════════════════════════════════


class MemoryContent(BaseModel):
    content: str


class MemoryStats(BaseModel):
    status: str
    count: int
    path: str = ""


class MemoryResetResponse(BaseModel):
    deleted: int
    index_cleared: bool = False


class MemoryItemOut(BaseModel):
    id: str
    memory: str
    created_at: str | None = None
    categories: list[str] = Field(default_factory=list)
    confirmation_status: str = "confirmed"


class AboutYouResponse(BaseModel):
    about_you: str = ""
    updated_at: str = ""
    patterns: list[dict[str, Any]] = Field(default_factory=list)
    now_status: dict[str, str] = Field(default_factory=dict)
    journey: list[dict[str, Any]] = Field(default_factory=list)


# ═══════════════════════════════════════════
#  活跃路由（文件优先）
# ═══════════════════════════════════════════


@router.get("/me", response_model=MemoryContent)
async def get_my_memory(user_id: str = Query("demo_user")) -> MemoryContent:
    """读取 MEMORY.md 全文。"""
    _validate_user_id(user_id)
    try:
        content = await _store.read_memory(user_id)
        return MemoryContent(content=content or "")
    except Exception as exc:
        logger.exception("Read memory failed: user_id=%s", user_id)
        raise HTTPException(status_code=500, detail="读取记忆失败") from exc


@router.put("/me")
async def put_my_memory(
    body: dict[str, str],
    user_id: str = Query("demo_user"),
) -> dict:
    """保存完整 MEMORY.md。"""
    _validate_user_id(user_id)
    content = body.get("content", "")
    try:
        await _store.write_memory(user_id, content)
        invalidate_system_prompt_cache(user_id)
        return {"message": "已保存", "chars": len(content)}
    except Exception as exc:
        logger.exception("Write memory failed: user_id=%s", user_id)
        raise HTTPException(status_code=500, detail="保存记忆失败") from exc


@router.get("/partner", response_model=MemoryContent)
async def get_partner_rules(user_id: str = Query("demo_user")) -> MemoryContent:
    """读取 PARTNER.md（AI 协作规则）。"""
    _validate_user_id(user_id)
    try:
        content = await _store.read_partner(user_id)
        return MemoryContent(content=content or "")
    except Exception as exc:
        logger.exception("Read partner rules failed: user_id=%s", user_id)
        raise HTTPException(status_code=500, detail="读取协作规则失败") from exc


@router.put("/partner")
async def put_partner_rules(
    body: dict[str, str],
    user_id: str = Query("demo_user"),
) -> dict:
    """保存完整 PARTNER.md。"""
    _validate_user_id(user_id)
    content = body.get("content", "")
    try:
        await _store.write_partner(user_id, content)
        invalidate_system_prompt_cache(user_id)
        return {"message": "已保存", "chars": len(content)}
    except Exception as exc:
        logger.exception("Write partner rules failed: user_id=%s", user_id)
        raise HTTPException(status_code=500, detail="保存协作规则失败") from exc


@router.get("/persona", response_model=MemoryContent)
async def get_persona(user_id: str = Query("demo_user")) -> MemoryContent:
    """读取 PERSONA.md（人格设定）。"""
    _validate_user_id(user_id)
    try:
        content = await _store.read_persona(user_id)
        return MemoryContent(content=content or "")
    except Exception as exc:
        logger.exception("Read persona failed: user_id=%s", user_id)
        raise HTTPException(status_code=500, detail="读取人格设定失败") from exc


@router.put("/persona")
async def put_persona(
    body: dict[str, str],
    user_id: str = Query("demo_user"),
) -> dict:
    """保存完整 PERSONA.md。"""
    _validate_user_id(user_id)
    content = body.get("content", "")
    try:
        await _store.write_persona(user_id, content)
        invalidate_system_prompt_cache(user_id)
        return {"message": "已保存", "chars": len(content)}
    except Exception as exc:
        logger.exception("Write persona failed: user_id=%s", user_id)
        raise HTTPException(status_code=500, detail="保存人格设定失败") from exc


@router.get("/stats", response_model=MemoryStats)
async def get_memory_stats(user_id: str = Query("demo_user")) -> MemoryStats:
    _validate_user_id(user_id)
    ensure_memory_dirs(user_id)
    path = str(memory_dir(user_id))
    try:
        content = await _store.read_memory(user_id)
        # 简单统计：按行数估算条目数
        lines = [ln for ln in content.splitlines() if ln.strip().startswith("- ")]
        return MemoryStats(status="ready", count=len(lines), path=path)
    except Exception as exc:
        logger.error("Memory stats failed: %s", exc)
        return MemoryStats(status="ready", count=0, path=path)


@router.post("/reset", response_model=MemoryResetResponse)
async def reset_memory(user_id: str = Query("demo_user")) -> MemoryResetResponse:
    """清空 MEMORY.md 和 USER.md。"""
    _validate_user_id(user_id)
    try:
        await _store.reset_user_memory(user_id)
        invalidate_system_prompt_cache(user_id)
        return MemoryResetResponse(deleted=0, index_cleared=True)
    except Exception as exc:
        logger.error("Memory reset failed: %s", exc)
        raise HTTPException(status_code=500, detail="清空失败，请查看日志")


@router.get("/understanding", response_model=AboutYouResponse)
async def get_ai_understanding(user_id: str = Query("demo_user")) -> AboutYouResponse:
    """获取 AI 综合画像（USER.md）。"""
    _validate_user_id(user_id)
    try:
        content = await _store.read_about_you(user_id)
        return AboutYouResponse(about_you=content or "")
    except Exception as exc:
        logger.error("AI understanding read failed: %s", exc)
        return AboutYouResponse()


@router.post("/understanding/refresh")
async def refresh_ai_understanding(user_id: str = Query("demo_user")) -> dict:
    """手动触发 AI 画像重新生成。"""
    _validate_user_id(user_id)
    try:
        from lib.memory.understanding import update_ai_understanding

        text = await update_ai_understanding(user_id)
        return {"message": "画像已更新", "chars": len(text)}
    except Exception as exc:
        logger.error("AI understanding refresh failed: %s", exc)
        raise HTTPException(status_code=500, detail="画像更新失败") from exc


@router.post("/understanding/correct")
async def correct_ai_understanding(
    body: dict[str, str],
    user_id: str = Query("demo_user"),
) -> dict:
    """用户手动纠正 AI 画像文本（直接覆写 USER.md）。"""
    _validate_user_id(user_id)
    corrected_text = body.get("text", "")
    if not corrected_text:
        raise HTTPException(status_code=400, detail="纠正内容不能为空")
    try:
        await _store.write_about_you(user_id, corrected_text)
        from lib.memory.understanding import _update_profile_data

        await _update_profile_data(user_id, corrected_text)
        invalidate_system_prompt_cache(user_id)
        return {"message": "已更新", "chars": len(corrected_text)}
    except Exception as exc:
        logger.error("AI understanding correct failed: %s", exc)
        raise HTTPException(status_code=500, detail="更新失败") from exc


@router.post("/tell")
async def tell_ai(
    body: dict[str, str],
    user_id: str = Query("demo_user"),
) -> dict:
    """用户主动告诉 AI，追加到 MEMORY.md。"""
    _validate_user_id(user_id)
    event_type = body.get("event_type", "general")
    content = body.get("content", "")

    if not content:
        raise HTTPException(status_code=400, detail="content 不能为空")

    try:
        await _store.append_memory_entry(user_id, event_type, content)
        # 触发 USER.md 刷新
        import contextlib

        from lib.memory.understanding import update_ai_understanding

        with contextlib.suppress(Exception):
            await update_ai_understanding(user_id)

        invalidate_system_prompt_cache(user_id)
        return {"message": "已记录"}
    except Exception as exc:
        logger.error("tell_ai failed: %s", exc)
        raise HTTPException(status_code=500, detail="记录失败") from exc


@router.get("/search")
async def search_memories(
    user_id: str = Query("demo_user"),
    query: str = Query(...),
) -> list[MemoryItemOut]:
    """搜索 MEMORY.md（简单文本匹配）。"""
    _validate_user_id(user_id)
    try:
        content = await _store.read_memory(user_id)
        if not content or not query:
            return []

        keywords = [kw.lower() for kw in query.split() if len(kw) > 1]
        if not keywords:
            return []

        paragraphs = content.split("\n\n")
        results: list[MemoryItemOut] = []
        for i, para in enumerate(paragraphs):
            para_lower = para.lower()
            if any(kw in para_lower for kw in keywords):
                results.append(
                    MemoryItemOut(
                        id=f"md-{i}",
                        memory=para[:300],
                        categories=[],
                    )
                )
        return results
    except Exception as exc:
        logger.error("Memory search failed: %s", exc)
        return []


# ═══════════════════════════════════════════
#  退役路由（返回兼容空结果，前端过渡用）
# ═══════════════════════════════════════════


@router.get("/list", response_model=list[MemoryItemOut])
async def list_memories(user_id: str = Query("demo_user")) -> list[MemoryItemOut]:
    """已退役：返回空列表。"""
    _validate_user_id(user_id)
    return []


@router.delete("/{event_id}")
async def delete_memory(event_id: str, user_id: str = Query("demo_user")) -> dict:
    """已退役：返回 404。"""
    _validate_user_id(user_id)
    raise HTTPException(status_code=404, detail="逐条删除已退役，请使用全文编辑")


@router.patch("/{event_id}")
async def update_memory(
    event_id: str,
    body: dict[str, Any],
    user_id: str = Query("demo_user"),
) -> dict:
    """已退役：返回 404。"""
    _validate_user_id(user_id)
    raise HTTPException(status_code=404, detail="逐条编辑已退役，请使用全文编辑")


@router.post("/{event_id}/review")
async def review_memory(
    event_id: str,
    body: dict[str, str],
    user_id: str = Query("demo_user"),
) -> dict:
    """已退役：返回 404。"""
    _validate_user_id(user_id)
    raise HTTPException(status_code=404, detail="逐条审核已退役")


@router.get("/observations")
async def get_observations(
    days: int = Query(7),
    user_id: str = Query("demo_user"),
) -> dict:
    """已退役：返回空观察。"""
    _validate_user_id(user_id)
    return {
        "observations": [],
        "generated_at": None,
        "events_analyzed": 0,
        "period_days": days,
    }


@router.post("/rebuild")
async def rebuild_memory(user_id: str = Query("demo_user")) -> dict:
    """已退役：新架构无需重建，直接返回成功。"""
    _validate_user_id(user_id)
    return {"message": "新架构无需重建", "user_id": user_id}
