"""画像服务 — 简历文本提取 + LLM 解析 + 写 DB"""

from __future__ import annotations

import json
import re
import logging
from datetime import datetime

from fastapi import UploadFile, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.backend.agent.llm_router import chat as llm_chat
from app.backend.models.user import User, UserProfile
from app.backend.schemas.profile import (
    ProfileResponse,
    ProfileUpdate,
    ResumeUploadResponse,
    SkillItem,
)

logger = logging.getLogger(__name__)

_MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB

# ═══════════════════════════════════════════════
# 文本提取
# ═══════════════════════════════════════════════

async def _extract_text(file: UploadFile) -> str:
    """从上传文件提取纯文本"""
    content = await file.read()
    if len(content) > _MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="文件超过 10MB 限制")

    filename = (file.filename or "").lower()

    if filename.endswith(".pdf") or (file.content_type == "application/pdf"):
        return _extract_pdf(content)
    elif filename.endswith(".docx") or "officedocument" in (file.content_type or ""):
        return _extract_docx(content)
    elif filename.endswith((".txt", ".md")) or (file.content_type or "").startswith("text/"):
        return content.decode("utf-8", errors="ignore")
    else:
        raise HTTPException(
            status_code=415,
            detail=f"不支持的文件类型。仅支持 PDF、DOCX、TXT、MD。",
        )


def _extract_pdf(content: bytes) -> str:
    """pdfplumber 提取 PDF 文本"""
    import pdfplumber
    from io import BytesIO

    with pdfplumber.open(BytesIO(content)) as pdf:
        pages = [p.extract_text() or "" for p in pdf.pages]
    text = "\n".join(pages)
    if not text.strip():
        raise HTTPException(status_code=422, detail="PDF 无法提取文本（可能是扫描件或加密文件）")
    return text


def _extract_docx(content: bytes) -> str:
    """python-docx 提取 DOCX 文本"""
    from docx import Document
    from io import BytesIO

    doc = Document(BytesIO(content))
    text = "\n".join(p.text for p in doc.paragraphs)
    if not text.strip():
        raise HTTPException(status_code=422, detail="DOCX 文件内容为空")
    return text


# ═══════════════════════════════════════════════
# LLM 解析 → UserProfile
# ═══════════════════════════════════════════════

_PROFILE_EXTRACT_PROMPT = """你是一个简历解析器。从以下简历文本中提取信息，返回 JSON。

提取规则：
1. **name**：简历上的姓名
2. **school_name**：学校全称（如"清华大学"）
3. **school_level**：从学校名推断 → "985" / "211" / "double_first_class" / "normal"
4. **major**：专业全称（如"计算机科学与技术"）
5. **graduation_year**：毕业年份（数字，如 2026）
6. **grade**：从毕业年份推断 → "freshman"(大一) / "sophomore"(大二) / "junior"(大三) / "senior"(大四) / "graduate1"~"graduate3"(研一~研三)。如果已毕业，根据毕业年份距今年数填 "graduate" 级别
7. **target_direction**：从技能栈和项目推断目标方向 → "后端" / "前端" / "算法" / "AI" / "测试" / "运维" / "安全" / "客户端" / "数据" / "嵌入式" / "其他"
8. **target_company_level**：从实习/项目经历推断目标公司级别 → "top"(大厂) / "major"(中厂) / "medium"(小厂) / "state_owned"(国企)。有顶级实习填 "top"，无则按项目质量判断
9. **current_skills**：技能列表，每个技能含 name 和 level（"beginner"/"familiar"/"intermediate"/"advanced"），从项目描述判断 level

要求：
- 只输出 JSON，不要解释
- 无法确定的字段填 null
- 当前年份是 {current_year} 年

简历文本：
{resume_text}
"""


async def _llm_extract(raw_text: str) -> dict:
    """调用 LLM 从简历文本提取结构化画像"""
    # 截断过长文本（前 5000 字符信息密度最高）
    truncated = raw_text[:5000]
    prompt = _PROFILE_EXTRACT_PROMPT.format(
        current_year=datetime.now().year,
        resume_text=truncated,
    )

    result = await llm_chat(
        task_type="skill_analysis",  # 低温度
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        max_tokens=1024,
    )

    return _parse_json(result)


def _parse_json(text: str) -> dict:
    """鲁棒 JSON 解析：处理 markdown fence、think tag、截断"""
    if not text:
        return {}

    # 去除 think/thinking/reasoning tag
    text = re.sub(r"<(think|thinking|reasoning)>.*?</\1>", "", text, flags=re.DOTALL | re.IGNORECASE)

    # 尝试提取 markdown code fence
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    candidate = fence_match.group(1).strip() if fence_match else text.strip()

    # 定位首尾花括号
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = candidate[start : end + 1]

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        logger.warning("JSON 解析失败，原始输出: %.200s", text)
        return {}


# ═══════════════════════════════════════════════
# DB 读写
# ═══════════════════════════════════════════════

async def _save_profile(db: AsyncSession, user_id: str, data: dict) -> ProfileResponse:
    """将 LLM 提取结果写入 User + UserProfile"""
    # 确保 User 记录存在
    user = await db.get(User, user_id)
    if user is None:
        user = User(user_id=user_id, nickname=data.get("name"))
        db.add(user)
        await db.flush()
        logger.info("自动创建用户: user_id=%s", user_id)
    elif data.get("name"):
        user.nickname = data["name"]

    # 2. 更新或创建 UserProfile
    result = await db.execute(
        select(UserProfile).where(UserProfile.user_id == user_id)
    )
    profile = result.scalar_one_or_none()

    if profile is None:
        profile = UserProfile(user_id=user_id)
        db.add(profile)

    # 映射字段
    profile.school_name = data.get("school_name")
    profile.school_level = data.get("school_level")
    profile.major = data.get("major")
    profile.grade = _map_grade(data.get("grade"))
    profile.graduation_year = data.get("graduation_year")
    profile.target_direction = _map_direction(data.get("target_direction"))
    profile.target_company_level = data.get("target_company_level")

    skills = data.get("current_skills")
    if skills:
        # 标准化为 [{"skill": name, "level": level}, ...]
        if isinstance(skills, list):
            normalized = []
            for s in skills:
                if isinstance(s, str):
                    normalized.append({"skill": s, "level": "familiar"})
                elif isinstance(s, dict):
                    normalized.append({
                        "skill": s.get("name", s.get("skill", "")),
                        "level": s.get("level", "familiar"),
                    })
            profile.current_skills = normalized

    await db.flush()
    return _profile_to_response(profile, data.get("name"))


def _map_grade(grade: str | None) -> str | None:
    """标准化年级值"""
    if not grade:
        return None
    valid = {
        "freshman", "sophomore", "junior", "senior",
        "graduate1", "graduate2", "graduate3",
    }
    return grade if grade in valid else None


def _map_direction(direction: str | None) -> str | None:
    """标准化方向值"""
    if not direction:
        return None
    valid = {"后端", "前端", "算法", "AI", "测试", "运维", "安全", "客户端", "数据", "嵌入式", "其他"}
    return direction if direction in valid else None


async def get_profile(db: AsyncSession, user_id: str) -> ProfileResponse:
    """读取用户画像"""
    result = await db.execute(
        select(UserProfile).where(UserProfile.user_id == user_id)
    )
    profile = result.scalar_one_or_none()

    # 同时查 nickname
    user = await db.get(User, user_id)
    nickname = user.nickname if user else None

    return _profile_to_response(profile, nickname) if profile else ProfileResponse(nickname=nickname)


async def update_profile(
    db: AsyncSession, user_id: str, patch: ProfileUpdate
) -> ProfileResponse:
    """局部更新用户画像"""
    result = await db.execute(
        select(UserProfile).where(UserProfile.user_id == user_id)
    )
    profile = result.scalar_one_or_none()

    if profile is None:
        profile = UserProfile(user_id=user_id)
        db.add(profile)

    patch_data = patch.model_dump(exclude_unset=True)
    skills = patch_data.pop("current_skills", None)
    nickname = patch_data.pop("nickname", None)

    for key, value in patch_data.items():
        if value is not None:
            setattr(profile, key, value)

    if skills is not None:
        profile.current_skills = [
            {"skill": s.name, "level": s.level} for s in skills
        ]

    if nickname is not None:
        user = await db.get(User, user_id)
        if user:
            user.nickname = nickname

    await db.flush()
    user = await db.get(User, user_id)
    return _profile_to_response(profile, user.nickname if user else None)


def _profile_to_response(profile: UserProfile | None, nickname: str | None) -> ProfileResponse:
    """ORM → 响应模型"""
    if profile is None:
        return ProfileResponse(nickname=nickname)

    raw_skills = profile.current_skills or []
    skills = []
    if isinstance(raw_skills, list):
        for s in raw_skills:
            if isinstance(s, dict):
                skills.append(SkillItem(
                    name=s.get("skill", s.get("name", "")),
                    level=s.get("level", "familiar"),
                ))

    return ProfileResponse(
        nickname=nickname,
        school_name=profile.school_name,
        school_level=profile.school_level,
        major=profile.major,
        grade=profile.grade,
        graduation_year=profile.graduation_year,
        target_direction=profile.target_direction,
        target_company_level=profile.target_company_level,
        current_skills=skills if skills else None,
    )


# ═══════════════════════════════════════════════
# 主流程：上传 → 提取 → 解析 → 保存
# ═══════════════════════════════════════════════

async def process_resume(
    db: AsyncSession, user_id: str, file: UploadFile
) -> ResumeUploadResponse:
    """完整管线：上传简历 → 返回画像"""
    filename = file.filename or "unknown"

    # 1. 文本提取
    try:
        raw_text = await _extract_text(file)
        logger.info("[1/4] 文本提取成功: %s, %d chars", filename, len(raw_text))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[1/4] 文本提取失败: %s", filename)
        raise HTTPException(status_code=422, detail=f"文件读取失败: {e}")

    # 2. LLM 解析
    try:
        data = await _llm_extract(raw_text)
        if not data:
            raise HTTPException(status_code=422, detail="LLM 未返回有效 JSON，请确认简历内容清晰可读")
        logger.info("[2/4] LLM 解析成功: fields=%s", list(data.keys()))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[2/4] LLM 解析失败")
        raise HTTPException(status_code=502, detail=f"AI 解析失败: {e}")

    # 3. 写入 DB
    try:
        profile = await _save_profile(db, user_id, data)
        logger.info("[3/4] 画像保存成功: user_id=%s", user_id)
    except Exception as e:
        logger.exception("[3/4] 画像保存失败")
        raise HTTPException(status_code=500, detail=f"数据保存失败: {e}")

    # 4. 返回结果
    preview = raw_text[:500].replace("\n", " ")
    return ResumeUploadResponse(profile=profile, raw_text_preview=preview)
