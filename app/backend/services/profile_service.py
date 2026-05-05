"""简历导入与画像长期记忆持久化。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from fastapi import HTTPException, UploadFile
from pydantic import BaseModel
from pydantic_ai import Agent

from app.backend.agent.llm_router import _get_model_identifier
from app.backend.agent.llm_router import chat as llm_chat
from app.backend.services.memory_service import extract_profile_fields

logger = logging.getLogger(__name__)

_MAX_FILE_SIZE = 10 * 1024 * 1024
_LLM_TEMPERATURE = 0.1
_LLM_MAX_TOKENS = 4096
_LLM_TRUNCATION_LENGTH = 5000
_PREVIEW_LENGTH = 500
_ALLOWED_SUFFIXES = {".pdf", ".docx", ".doc", ".txt", ".md", ".html", ".htm"}

_PROFILE_EXTRACT_PROMPT = """从简历中提取用户核心画像，并直接输出 markdown。

输出要求：
1. 只输出 markdown，不要解释。
2. 如果信息缺失，写“（待填写）”。
3. 严格使用下面结构：

# 用户核心记忆

> 这个文件由 AI 自动管理，记录用户的核心信息。
> 每次对话开始时会自动注入到 system prompt。

## 基础信息
- 学校：...
- 专业：...
- 年级：...
- 毕业年份：...
- 学校层次：...

## 目标方向
- 目标岗位：...
- 目标公司类型：...
- 意向城市：...

## 教育背景
- GPA：...
- 排名：...
- 获奖：
  - ...

## 当前状态
- 正在学习：（待填写）
- 正在准备：（待填写）
- 焦虑程度：（待填写）

## 个人简介
...

## 英语水平
- ...

## 期望薪资
- ...

简历内容：
{resume_text}
"""


async def _extract_text(file: UploadFile) -> str:
    content = await file.read()
    if len(content) > _MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="文件超过 10MB 限制")

    filename = file.filename or "unknown"
    return await _extract_with_markitdown(content, filename)


async def _extract_with_markitdown(content: bytes, filename: str) -> str:
    import asyncio
    import tempfile

    from markitdown import MarkItDown

    suffix = Path(filename).suffix.lower() or ".tmp"
    if suffix not in _ALLOWED_SUFFIXES:
        raise HTTPException(status_code=415, detail=f"不支持的文件类型: {suffix}")

    def _convert() -> str:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)
        try:
            md = MarkItDown()
            result = md.convert(str(tmp_path))
            text = result.text_content
            if not text or not text.strip():
                raise ValueError("markitdown returned empty content")
            return text
        finally:
            tmp_path.unlink(missing_ok=True)

    return await asyncio.to_thread(_convert)


async def _llm_extract_to_markdown(raw_text: str) -> str:
    truncated = raw_text[:_LLM_TRUNCATION_LENGTH]
    prompt = _PROFILE_EXTRACT_PROMPT.format(
        resume_text=truncated,
    )
    result = await llm_chat(
        task_type="skill_analysis",
        messages=[
            {
                "role": "system",
                "content": "你是一个简历解析助手。只输出 markdown，不要输出解释。",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=_LLM_TEMPERATURE,
        max_tokens=_LLM_MAX_TOKENS,
    )
    result = result.strip()
    if not result.startswith("#"):
        lines = result.split("\n")
        md_start = next((i for i, line in enumerate(lines) if line.startswith("#")), 0)
        result = "\n".join(lines[md_start:])
    return result


# ── 简历结构化拆解 ──


class _ResumeSkill(BaseModel):
    name: str
    level: Literal["familiar", "proficient", "expert"] = "familiar"
    context: str = ""


class _ResumeExperience(BaseModel):
    title: str
    description: str = ""
    period: str = ""
    tech_stack: str = ""
    role: str = ""


class _ResumeDecomposition(BaseModel):
    skills: list[_ResumeSkill] = []
    experiences: list[_ResumeExperience] = []


_resume_decompose_agent = Agent(
    _get_model_identifier("skill_analysis"),
    result_type=_ResumeDecomposition,
    system_prompt="从简历 markdown 中提取技能列表和经历列表。只提取明确提到的内容，不要编造。",
)


async def _decompose_resume(markdown: str) -> _ResumeDecomposition:
    """用 PydanticAI Agent 从简历 markdown 拆解技能和经历。失败返回空。"""
    try:
        result = await _resume_decompose_agent.run(markdown[:_LLM_TRUNCATION_LENGTH])
        return result.data
    except Exception:
        logger.warning("Resume decomposition failed, continuing without skills/experiences")
        return _ResumeDecomposition()


async def process_resume_to_memory(file: UploadFile, user_id: str = "demo_user") -> dict:
    filename = file.filename or "unknown"

    try:
        raw_text = await _extract_text(file)
        logger.info("[1/3] Text extracted: %s, %d chars", filename, len(raw_text))
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[1/3] Text extraction failed: %s", filename)
        raise HTTPException(status_code=422, detail=f"文件读取失败: {exc}")

    try:
        markdown_content = await _llm_extract_to_markdown(raw_text)
        if not markdown_content or len(markdown_content) < 50:
            raise HTTPException(status_code=422, detail="LLM 未返回有效内容，请确认简历内容清晰可读")
        logger.info("[2/3] LLM extraction complete: %d chars", len(markdown_content))
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[2/3] LLM extraction failed")
        raise HTTPException(status_code=502, detail=f"AI 解析失败: {exc}")

    # [2.5/3] 结构化拆解
    try:
        profile_fields = extract_profile_fields(markdown_content)
        logger.info("[2.5/3] Profile fields extracted: %d fields", len(profile_fields))
    except Exception as exc:
        logger.warning("[2.5/3] Profile field extraction failed: %s", exc)
        profile_fields = {}

    try:
        decomp = await _decompose_resume(markdown_content)
        logger.info(
            "[2.5/3] Decomposition: %d skills, %d experiences",
            len(decomp.skills),
            len(decomp.experiences),
        )
    except Exception as exc:
        logger.warning("[2.5/3] Decomposition failed: %s", exc)
        decomp = _ResumeDecomposition()

    try:
        from app.backend.db.base import get_async_session_maker
        from app.backend.schemas.memory_events import (
            ExperiencePayload,
            ProfilePayload,
            SkillPayload,
        )
        from app.backend.services.cognee_projector import project_event_ids
        from app.backend.services.growth_event_service import create_growth_event_with_dedup
        from app.backend.services.md_projector import sync_user_md_projection

        event_ids: list[str] = []
        async with get_async_session_maker()() as db:
            # resume_uploaded 审计事件
            uploaded_event = await create_growth_event_with_dedup(
                db=db,
                user_id=user_id,
                event_type="resume_uploaded",
                entity_type="profile",
                entity_id=filename,
                payload={"filename": filename, "content_length": len(markdown_content)},
                source="简历提取",
            )
            if uploaded_event:
                event_ids.append(str(uploaded_event.id))

            # profile_updated：结构化字段
            if profile_fields:
                profile_payload = ProfilePayload(**profile_fields).model_dump(exclude_none=True)
                profile_event = await create_growth_event_with_dedup(
                    db=db,
                    user_id=user_id,
                    event_type="profile_updated",
                    entity_type="profile",
                    entity_id="resume_fields",
                    payload=profile_payload,
                    source="简历提取",
                )
                if profile_event:
                    event_ids.append(str(profile_event.id))

            # skill_added × N
            for skill in decomp.skills:
                payload = SkillPayload(
                    name=skill.name,
                    level=skill.level,
                    context=skill.context,
                    source="简历解析",
                ).model_dump()
                skill_event = await create_growth_event_with_dedup(
                    db=db,
                    user_id=user_id,
                    event_type="skill_added",
                    entity_type="skill",
                    entity_id=skill.name,
                    payload=payload,
                    source="简历提取",
                )
                if skill_event:
                    event_ids.append(str(skill_event.id))

            # experience_added × N
            for exp in decomp.experiences:
                payload = ExperiencePayload(
                    title=exp.title,
                    description=exp.description,
                    period=exp.period,
                    tech_stack=exp.tech_stack,
                    role=exp.role,
                    source="简历解析",
                ).model_dump()
                exp_event = await create_growth_event_with_dedup(
                    db=db,
                    user_id=user_id,
                    event_type="experience_added",
                    entity_type="experience",
                    entity_id=exp.title,
                    payload=payload,
                    source="简历提取",
                )
                if exp_event:
                    event_ids.append(str(exp_event.id))

            await db.commit()

        projected = await sync_user_md_projection(user_id)
        if not projected:
            raise HTTPException(status_code=500, detail="简历事件已写入，但画像投影失败")

        if event_ids:
            await project_event_ids(event_ids)

        logger.info("[3/3] Resume events persisted and markdown synchronized")
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("[3/3] Event persistence failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"简历持久化失败: {exc}")

    preview = raw_text[:_PREVIEW_LENGTH].replace("\n", " ")
    return {
        "success": True,
        "message": "简历解析成功，已写入长期记忆并同步画像",
        "preview": preview,
        "content_length": len(markdown_content),
    }
