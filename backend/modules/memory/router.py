"""记忆管理 API — 路由层，业务逻辑委托到 application"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.core.logging import get_logger
from backend.modules.memory.observations import ObservationsResult, synthesize_observations

logger = get_logger(__name__)

router = APIRouter(prefix="/memory", tags=["memory"])

_USER_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _validate_user_id(user_id: str) -> str:
    if not _USER_ID_PATTERN.match(user_id):
        raise HTTPException(status_code=400, detail="user_id 格式无效，只允许字母、数字、下划线和连字符，长度 1-64")
    return user_id


def _get_memory():
    from backend.modules.memory.facade import get_memory

    return get_memory()


class MemoryContent(BaseModel):
    content: str


class MemoryStats(BaseModel):
    status: str
    count: int


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


@router.get("/me", response_model=MemoryContent)
async def get_my_memory(user_id: str = Query("demo_user")) -> MemoryContent:
    _validate_user_id(user_id)
    try:
        content = await _get_memory().get_memory_content(user_id)
        return MemoryContent(content=content)
    except Exception:
        logger.exception("Read memory failed: user_id=%s", user_id)
        raise HTTPException(status_code=500, detail="读取画像失败") from None


@router.get("/stats", response_model=MemoryStats)
async def get_memory_stats(user_id: str = Query("demo_user")) -> MemoryStats:
    _validate_user_id(user_id)
    memory = _get_memory()
    status = "ready"
    try:
        count = await memory.count_events(user_id)
    except Exception as exc:
        logger.error("Memory stats count failed: %s", exc)
        count = 0
    return MemoryStats(status=status, count=count)


@router.post("/reset", response_model=MemoryResetResponse)
async def reset_memory(user_id: str = Query("demo_user")) -> MemoryResetResponse:
    _validate_user_id(user_id)
    try:
        result = await _get_memory().reset(user_id)
        return MemoryResetResponse(**result)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Memory reset failed: %s", exc)
        raise HTTPException(status_code=500, detail="清空失败，请查看日志")


@router.get("/list", response_model=list[MemoryItemOut])
async def list_memories(user_id: str = Query("demo_user")) -> list[MemoryItemOut]:
    _validate_user_id(user_id)
    try:
        items = await _get_memory().list_events(user_id)
    except Exception as exc:
        logger.error("Memory list failed: %s", exc)
        return []
    return [MemoryItemOut(**item) for item in items]


@router.post("/rebuild")
async def rebuild_memory(user_id: str = Query("demo_user")) -> dict:
    _validate_user_id(user_id)
    try:
        result = await _get_memory().rebuild(user_id)
        md_ok = result.get("md_success", False)
        msg = "重建成功"
        if not md_ok:
            msg = ".md 重建失败"
        return {"message": msg, "user_id": user_id, **result}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Memory rebuild failed: %s", exc)
        raise HTTPException(status_code=500, detail="重建失败，请查看日志")


@router.get("/observations", response_model=ObservationsResult)
async def get_observations(
    days: int = Query(7),
    user_id: str = Query("demo_user"),
) -> ObservationsResult:
    """返回 Lumen 关于当前用户的最新 3 条观察。

    events < 10 条时返回 observations=[]，前端不渲染。
    """
    if not (1 <= days <= 90):
        raise HTTPException(status_code=400, detail="days 必须在 1-90 之间")
    _validate_user_id(user_id)
    return await synthesize_observations(user_id=user_id, days=days)


@router.get("/search")
async def search_memories(
    user_id: str = Query("demo_user"),
    query: str = Query(...),
    limit: int = Query(10),
) -> list[MemoryItemOut]:
    _validate_user_id(user_id)
    try:
        memory = _get_memory()
        items = await memory.recall(user_id, query, limit=limit)
        return [
            MemoryItemOut(id=item.id, memory=item.content, created_at=item.created_at, categories=item.categories)
            for item in items
        ]
    except Exception as exc:
        logger.error("Memory search failed: %s", exc)
        return []


@router.delete("/{event_id}")
async def delete_memory(event_id: str, user_id: str = Query("demo_user")) -> dict:
    _validate_user_id(user_id)
    try:
        success, error = await _get_memory().delete_event(user_id, event_id)
        if not success:
            status_code = 403 if error and "无权" in error else 404
            raise HTTPException(status_code=status_code, detail=error or "删除失败")
        logger.info("Memory deleted: id=%s, user_id=%s", event_id, user_id)
        return {"deleted": event_id}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Memory delete failed: %s", exc)
        raise HTTPException(status_code=500, detail="删除失败")


@router.get("/understanding", response_model=AboutYouResponse)
async def get_ai_understanding(user_id: str = Query("demo_user")) -> AboutYouResponse:
    """获取 AI 综合画像（关于你 + 模式洞察 + 此刻状态 + 时间线）。"""
    _validate_user_id(user_id)
    try:
        from backend.modules.memory.understanding import get_about_you_data

        data = await get_about_you_data(user_id)
        return AboutYouResponse(
            about_you=data.about_you,
            updated_at=data.updated_at,
            patterns=data.patterns,
            now_status=data.now_status,
            journey=data.journey,
        )
    except Exception as exc:
        logger.error("AI understanding read failed: %s", exc)
        return AboutYouResponse()


@router.post("/understanding/refresh")
async def refresh_ai_understanding(user_id: str = Query("demo_user")) -> dict:
    """手动触发 AI 画像重新生成。"""
    _validate_user_id(user_id)
    try:
        from backend.modules.memory.understanding import update_ai_understanding

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
    """用户手动纠正 AI 画像文本。"""
    _validate_user_id(user_id)
    corrected_text = body.get("text", "")
    if not corrected_text:
        raise HTTPException(status_code=400, detail="纠正内容不能为空")
    from backend.modules.memory.markdown import write_about_you

    write_about_you(user_id, corrected_text)
    from backend.modules.memory.understanding import _update_profile_data

    await _update_profile_data(user_id, corrected_text)
    return {"message": "已更新", "chars": len(corrected_text)}


@router.patch("/{event_id}")
async def update_memory(
    event_id: str,
    body: dict[str, Any],
    user_id: str = Query("demo_user"),
) -> dict:
    """更新记忆内容。"""
    _validate_user_id(user_id)
    content = body.get("content", "")
    if not content:
        raise HTTPException(status_code=400, detail="content 不能为空")

    try:
        memory = _get_memory()
        success, error = await memory.update_event(
            user_id=user_id,
            event_id=event_id,
            payload={"key": content[:32], "value": content, "source": "用户编辑"},
        )
        if not success:
            raise HTTPException(status_code=404, detail=error or "更新失败")
        return {"updated": event_id}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Memory update failed: %s", exc)
        raise HTTPException(status_code=500, detail="更新失败")


class ReviewBody(BaseModel):
    status: str


@router.post("/{event_id}/review")
async def review_memory(
    event_id: str,
    body: ReviewBody,
    user_id: str = Query("demo_user"),
) -> dict:
    """审核记忆：confirmed 或 rejected。"""
    _validate_user_id(user_id)
    try:
        memory = _get_memory()
        success, error = await memory.review_event(
            user_id=user_id,
            event_id=event_id,
            status=body.status,
        )
        if not success:
            status_code = 400 if error and "必须是" in error else 404
            raise HTTPException(status_code=status_code, detail=error or "审核失败")
        return {"reviewed": event_id, "status": body.status}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Memory review failed: %s", exc)
        raise HTTPException(status_code=500, detail="审核失败")


@router.post("/tell")
async def tell_ai(
    body: dict[str, str],
    user_id: str = Query("demo_user"),
) -> dict:
    """用户主动告诉 AI 关于自己的信息，直接写入对应事件类型。"""
    _validate_user_id(user_id)
    event_type = body.get("event_type", "")
    content = body.get("content", "")

    if not event_type or not content:
        raise HTTPException(status_code=400, detail="event_type 和 content 不能为空")

    valid_types = {
        "interest": "interest_observed",
        "value": "value_surfaced",
        "relationship": "relationship_noted",
        "moment": "significant_moment",
        "reflection": "reflection_added",
    }
    if event_type not in valid_types:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的 event_type，支持: {', '.join(valid_types.keys())}",
        )

    try:
        memory = _get_memory()
        event = await memory.remember(
            user_id=user_id,
            event_type=valid_types[event_type],
            entity_type=event_type,
            entity_id=content[:32],
            payload={"key": content[:32], "value": content, "source": "用户主动"},
            source="用户主动",
        )
        if event and event.id:
            return {"message": "已记录", "event_id": str(event.id)}
        return {"message": "内容未变化，跳过"}
    except Exception as exc:
        logger.error("tell_ai failed: %s", exc)
        raise HTTPException(status_code=500, detail="记录失败") from exc
