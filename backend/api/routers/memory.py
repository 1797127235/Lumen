"""记忆管理 API — 路由层，业务逻辑委托到 application"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from backend.application.memory_service import (
    get_profile_structured as _get_profile_structured,
)
from backend.application.memory_service import (
    update_profile_structured as _update_profile_structured,
)
from backend.application.memory_service import (
    upload_resume as _upload_resume,
)
from backend.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/memory", tags=["memory"])

_USER_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _validate_user_id(user_id: str) -> str:
    if not _USER_ID_PATTERN.match(user_id):
        raise HTTPException(status_code=400, detail="user_id 格式无效，只允许字母、数字、下划线和连字符，长度 1-64")
    return user_id


def _get_memory():
    from backend.memory.facade import get_memory

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


class ResumeUploadResponse(BaseModel):
    ok: bool
    events: int
    profile: dict[str, Any] = Field(default_factory=dict)
    skills: list[dict[str, Any]] = Field(default_factory=list)
    experiences: list[dict[str, Any]] = Field(default_factory=list)


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
    status = memory.cognee_status()
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
        cognee_ok = result.get("cognee_success")
        msg = "重建成功"
        if not md_ok:
            msg = ".md 重建失败"
        elif cognee_ok is False:
            msg = ".md 已重建，但 Cognee 重建失败"
        return {"message": msg, "user_id": user_id, **result}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Memory rebuild failed: %s", exc)
        raise HTTPException(status_code=500, detail="重建失败，请查看日志")


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


@router.post("/compensate")
async def compensate_cognee(user_id: str = Query("demo_user")) -> dict:
    _validate_user_id(user_id)
    try:
        fixed = await _get_memory().compensate_cognee(user_id)
        return {"user_id": user_id, "compensated": fixed}
    except Exception as exc:
        logger.error("Cognee compensate failed: %s", exc)
        raise HTTPException(status_code=500, detail="补偿失败")


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


@router.post("/upload-resume", response_model=ResumeUploadResponse)
async def upload_resume(
    file: UploadFile = File(...),
    user_id: str = Form("demo_user"),
) -> ResumeUploadResponse:
    _validate_user_id(user_id)

    try:
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="上传文件为空")
    except Exception as exc:
        logger.error("Resume read failed: %s", exc)
        raise HTTPException(status_code=400, detail="文件读取失败") from exc

    try:
        result = await _upload_resume(content=content, filename=file.filename or "resume.pdf", user_id=user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Resume upload failed: %s", exc)
        raise HTTPException(status_code=500, detail="简历解析失败") from exc

    return ResumeUploadResponse(
        ok=True,
        events=result["events_count"],
        profile=result["profile"],
        skills=result["skills"],
        experiences=result["experiences"],
    )


class SkillItemOut(BaseModel):
    name: str
    level: str
    context: str | None = None
    source: str | None = None


class ExperienceItemOut(BaseModel):
    title: str
    description: str
    period: str | None = None
    tech_stack: str | None = None
    role: str | None = None
    source: str | None = None


class ProfileStructuredResponse(BaseModel):
    profile: dict[str, Any] = Field(default_factory=dict)
    skills: list[SkillItemOut] = Field(default_factory=list)
    experiences: list[ExperienceItemOut] = Field(default_factory=list)
    goals: dict[str, str] = Field(default_factory=dict)
    preferences: dict[str, str] = Field(default_factory=dict)
    status: dict[str, str] = Field(default_factory=dict)
    decisions: list[dict[str, Any]] = Field(default_factory=list)


class AboutYouResponse(BaseModel):
    about_you: str = ""
    updated_at: str = ""
    patterns: list[dict[str, Any]] = Field(default_factory=list)
    now_status: dict[str, str] = Field(default_factory=dict)
    journey: list[dict[str, Any]] = Field(default_factory=list)


class ProfileUpdateRequest(BaseModel):
    """前端提交的画像更新请求 — 传什么字段就更新什么。"""

    profile: dict[str, Any] | None = None
    skills: list[dict[str, Any]] | None = None
    experiences: list[dict[str, Any]] | None = None


@router.get("/profile-structured", response_model=ProfileStructuredResponse)
async def get_profile_structured(user_id: str = Query("demo_user")) -> ProfileStructuredResponse:
    """读取用户画像的结构化数据（从 GrowthEvent 合并，不是解析 markdown）。"""
    _validate_user_id(user_id)

    try:
        data = await _get_profile_structured(user_id)
    except Exception as exc:
        logger.error("Profile structured read failed: %s", exc)
        raise HTTPException(status_code=500, detail="读取画像失败") from exc

    skills_out = [
        SkillItemOut(
            name=s["name"],
            level=s.get("level", "familiar"),
            context=s.get("context"),
            source=s.get("source"),
        )
        for s in data["skills"]
    ]

    experiences_out = [
        ExperienceItemOut(
            title=e["title"],
            description=e.get("description", ""),
            period=e.get("period"),
            tech_stack=e.get("tech_stack"),
            role=e.get("role"),
            source=e.get("source"),
        )
        for e in data["experiences"]
    ]

    return ProfileStructuredResponse(
        profile=data["profile"],
        skills=skills_out,
        experiences=experiences_out,
        goals=data["goals"],
        preferences=data["preferences"],
        status=data["status"],
        decisions=data["decisions"],
    )


@router.post("/profile-update")
async def update_profile_structured(
    req: ProfileUpdateRequest,
    user_id: str = Query("demo_user"),
) -> dict:
    """前端手动编辑画像后批量提交 — 生成对应 GrowthEvent。"""
    _validate_user_id(user_id)

    try:
        count = await _update_profile_structured(
            user_id=user_id,
            profile=req.profile,
            skills=req.skills,
            experiences=req.experiences,
        )
    except Exception as exc:
        logger.error("Profile structured update failed: %s", exc)
        raise HTTPException(status_code=500, detail="更新失败，请查看日志") from exc

    if count == 0:
        return {"updated": 0, "message": "没有需要更新的内容"}
    return {"updated": count, "message": f"已更新 {count} 条记录"}


@router.get("/understanding", response_model=AboutYouResponse)
async def get_ai_understanding(user_id: str = Query("demo_user")) -> AboutYouResponse:
    """获取 AI 综合画像（关于你 + 模式洞察 + 此刻状态 + 时间线）。"""
    _validate_user_id(user_id)
    try:
        from backend.memory.understanding import get_about_you_data

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
        from backend.memory.understanding import update_ai_understanding

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
    from backend.memory.markdown import write_about_you

    write_about_you(user_id, corrected_text)
    from backend.memory.understanding import _update_profile_data

    await _update_profile_data(user_id, corrected_text)
    return {"message": "已更新", "chars": len(corrected_text)}
