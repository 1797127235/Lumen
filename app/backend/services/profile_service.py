"""画像服务 — 简历文本提取 + LLM 解析 + 写 DB"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime

from fastapi import HTTPException, UploadFile
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.backend.agent.llm_router import chat as llm_chat
from app.backend.models.user import User, UserProfile
from app.backend.schemas.profile import (
    PortfolioLink,
    ProfileResponse,
    ProfileUpdate,
    ProjectItem,
    ResumeUploadResponse,
    SkillItem,
    WorkExperienceItem,
)
from app.backend.utils.date_utils import restore_dates
from app.backend.utils.json_utils import extract_json

logger = logging.getLogger(__name__)

# ── 文件限制 ──
_MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB

# ── LLM 参数 ──
_LLM_TEMPERATURE = 0.1
_LLM_MAX_TOKENS = 4096
_LLM_TRUNCATION_LENGTH = 5000  # 截断过长文本（前 N 字符信息密度最高）
_MAX_RETRIES = 3  # JSON 截断时重试次数

# ── 预览 ──
_PREVIEW_LENGTH = 500  # 简历文本预览长度

# ── 默认值 ──
_DEFAULT_SKILL_LEVEL = "familiar"

# 允许上传的简历文件扩展名白名单
_ALLOWED_SUFFIXES = {".pdf", ".docx", ".doc", ".txt", ".md", ".html", ".htm"}


async def _extract_text(file: UploadFile) -> str:
    content = await file.read()
    if len(content) > _MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="文件超过 10MB 限制")

    filename = file.filename or "unknown"
    return await asyncio.to_thread(_extract_with_markitdown, content, filename)


def _extract_with_markitdown(content: bytes, filename: str) -> str:
    """使用 markitdown 将文件转为 Markdown 文本。"""
    import tempfile
    from pathlib import Path

    from markitdown import MarkItDown

    suffix = Path(filename).suffix.lower() or ".tmp"
    if suffix not in _ALLOWED_SUFFIXES:
        raise HTTPException(status_code=415, detail=f"不支持的文件类型: {suffix}")
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        md = MarkItDown()
        result = md.convert(str(tmp_path))
        text = result.text_content
        if not text or not text.strip():
            raise ValueError("markitdown 返回空内容")
        return text
    finally:
        tmp_path.unlink(missing_ok=True)


# ═══════════════════════════════════════════════
# LLM 解析 → UserProfile
# ═══════════════════════════════════════════════

_PROFILE_SCHEMA_EXAMPLE = """{
  "name": "张三",
  "school_name": "清华大学",
  "school_level": "985",
  "major": "计算机科学与技术",
  "graduation_year": 2026,
  "grade": "junior",
  "target_direction": "后端",
  "target_company_level": "top",
  "bio": "热爱技术的CS学生，有扎实的算法基础和项目经验",
  "city": "北京",
  "english_level": "CET-6 550",
  "expected_salary": "15k-20k",
  "work_experience": [
    {
      "company": "字节跳动",
      "role": "后端开发实习",
      "period": "2024.06 - 2024.09",
      "description": "Go + Kitex 做内容审核中台，日均5亿请求"
    }
  ],
  "projects": [
    {
      "title": "校园二手书交易平台",
      "tech_stack": "Spring Boot + MySQL + Redis",
      "role": "后端开发",
      "period": "2023.09 - 2024.01",
      "description": "小组4人项目，负责后端。上线后注册用户2k+。"
    },
    {
      "title": "高性能网络库",
      "tech_stack": "C++ + epoll + Reactor模式",
      "role": "独立开发",
      "period": "2025.03 - 2025.06",
      "description": "基于Reactor模型实现非阻塞IO网络库，支持高并发"
    }
  ],
  "current_skills": [
    { "name": "Python", "level": "advanced", "context": "竞赛主力语言" },
    { "name": "Go", "level": "intermediate", "context": "实习中常用" }
  ],
  "education": {
    "gpa": "3.8/4.0",
    "ranking": "前10%",
    "awards": ["国家奖学金", "ACM 银牌"]
  },
  "portfolio_links": [
    { "label": "GitHub", "url": "https://github.com/xxx" }
  ]
}"""

_PROFILE_EXTRACT_PROMPT = """Parse this resume into JSON. Output ONLY the JSON object, no other text.

Example output format:
{schema}

Rules:
- Use "" for missing text fields, [] for missing arrays, null for missing optional fields
- projects[] is REQUIRED whenever the resume has a project block (e.g. Chinese headings: 项目经验 / 项目经历 / 个人项目 / 课程设计 / 科研经历). Output one object per project with title, period, role, tech_stack, description taken from the resume. Do NOT leave projects empty while copying the same narrative only into current_skills[].context — skills should be short proficiency notes; long bullet lists under a project belong in projects[].description.
- Extract ALL other projects mentioned only inside skill paragraphs as separate projects[] entries too.
- skill level: "beginner" / "familiar" / "intermediate" / "advanced"
- school_level: "985" / "211" / "double_first_class" / "normal"
- grade: "freshman" / "sophomore" / "junior" / "senior" / "graduate1" / "graduate2" / "graduate3"
- target_direction: "后端" / "前端" / "算法" / "AI" / "测试" / "运维" / "安全" / "客户端" / "数据" / "嵌入式" / "其他"
- target_company_level: "top" / "major" / "medium" / "state_owned"
- Do NOT add any skill, project, or achievement not explicitly mentioned in the resume
- Do NOT upgrade skill levels or embellish descriptions
- Current year is {current_year}

Resume to parse:
{resume_text}"""


_PROJECT_HEADINGS = ("项目经验", "项目经历", "个人项目", "科研经历")
_PROJECT_END_HEADINGS = (
    "技能特长",
    "专业技能",
    "技能",
    "实习经历",
    "工作经历",
    "教育背景",
    "荣誉奖项",
    "获奖经历",
    "自我评价",
    "个人评价",
)
_PERIOD_LINE_RE = re.compile(
    r"^\d{4}[-./]\d{1,2}\s*[~\-–—]\s*(?:\d{4}[-./]\d{1,2}|至今|现在|Present|Current)$",
    re.IGNORECASE,
)


def _is_truncated(data: dict, resume_text: str) -> bool:
    """检测解析结果是否可能被截断，或漏填 projects（模型常把项目写进技能 context）。"""
    if not isinstance(data, dict):
        return True
    for key in ("current_skills", "education"):
        if key in data and data[key] == []:
            return True
    if any(h in resume_text for h in _PROJECT_HEADINGS):
        pr = data.get("projects")
        if not isinstance(pr, list) or len(pr) == 0:
            return True
    return False


_RETRY_PROJECTS_NUDGE = (
    "\n\n[Retry instruction] The resume contains a project section (e.g. 项目经验). "
    "Your previous JSON had empty or missing projects[]. Fill projects[] with one object per "
    "project from that section (title, period, role, tech_stack, description). Output ONLY JSON."
)


async def _llm_extract(raw_text: str) -> dict:
    """调用 LLM 从简历文本提取结构化画像"""
    truncated = raw_text[:_LLM_TRUNCATION_LENGTH]
    base_prompt = _PROFILE_EXTRACT_PROMPT.format(
        schema=_PROFILE_SCHEMA_EXAMPLE,
        current_year=datetime.now().year,
        resume_text=truncated,
    )

    for attempt in range(_MAX_RETRIES):
        user_content = base_prompt + (_RETRY_PROJECTS_NUDGE if attempt > 0 else "")
        result = await llm_chat(
            task_type="skill_analysis",
            messages=[
                {
                    "role": "system",
                    "content": "You are a JSON extraction engine. Output only valid JSON, no explanations.",
                },
                {"role": "user", "content": user_content},
            ],
            temperature=_LLM_TEMPERATURE,
            max_tokens=_LLM_MAX_TOKENS,
        )

        data = extract_json(result)
        if _is_truncated(data, truncated) and attempt < _MAX_RETRIES - 1:
            logger.warning("LLM 输出可能被截断或漏填 projects，重试 (%d/%d)", attempt + 1, _MAX_RETRIES)
            continue
        data = restore_dates(data, raw_text)
        _ensure_projects_from_text(data, raw_text)
        return data
    return {}


def _ensure_projects_from_text(data: dict, raw_text: str) -> None:
    """LLM 漏填 projects 时，从简历的项目区块做确定性兜底解析。"""
    if not isinstance(data, dict):
        return
    if isinstance(data.get("projects"), list) and data["projects"]:
        return
    projects = _extract_projects_from_project_section(raw_text)
    if projects:
        data["projects"] = projects


def _extract_projects_from_project_section(raw_text: str) -> list[dict]:
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    start = next(
        (i for i, line in enumerate(lines) if any(h in line for h in _PROJECT_HEADINGS)),
        None,
    )
    if start is None:
        return []

    end = len(lines)
    for i in range(start + 1, len(lines)):
        if any(h in lines[i] for h in _PROJECT_END_HEADINGS):
            end = i
            break

    section = lines[start + 1 : end]
    projects: list[dict] = []
    i = 0
    while i < len(section):
        if not _PERIOD_LINE_RE.match(section[i]):
            i += 1
            continue

        period = section[i]
        title = section[i + 1] if i + 1 < len(section) else ""
        role = None
        body_start = i + 2
        if body_start < len(section) and _looks_like_project_role(section[body_start]):
            role = section[body_start]
            body_start += 1

        j = body_start
        body: list[str] = []
        while j < len(section) and not _PERIOD_LINE_RE.match(section[j]):
            body.append(section[j])
            j += 1

        tech_stack = _extract_labeled_value(body, "技术栈")
        description_parts = [_clean_project_line(part) for part in body if not part.startswith("【源码地址】")]
        description = " ".join(part for part in description_parts if part).strip()
        if title and description:
            projects.append(
                {
                    "title": title,
                    "tech_stack": tech_stack,
                    "role": role,
                    "period": period,
                    "description": description,
                }
            )
        i = j

    return projects


def _looks_like_project_role(value: str) -> bool:
    return (
        len(value) <= 20
        and not value.startswith("【")
        and not value.startswith("·")
        and not _PERIOD_LINE_RE.match(value)
    )


def _extract_labeled_value(parts: list[str], label: str) -> str | None:
    prefix = f"【{label}】"
    for part in parts:
        if part.startswith(prefix):
            return part.removeprefix(prefix).strip(" ：:")
    return None


def _clean_project_line(value: str) -> str:
    """移除项目描述行中的标签前缀（如【技术栈】）和源码地址标记。"""
    return re.sub(r"^【[^】]+】\s*|【源码地址】", "", value).strip()


# ═══════════════════════════════════════════════
# DB 读写
# ═══════════════════════════════════════════════


def _set_if_exists(obj, field: str, value):
    """仅当 value 非 None 时才设置字段，防止空数据覆盖已有值"""
    if value is not None:
        setattr(obj, field, value)


def _set_ext(pdata: dict, data: dict, key: str, default=None):
    """条件写入 profile_data 扩展字段：有值写入，None 时删除。"""
    v = data.get(key)
    if v is not None and v != [] and v != "" and v != default:
        pdata[key] = v
    elif v is None:
        pdata.pop(key, None)


def _build_list_from_pdata(pdata: dict, key: str, factory):
    """从 profile_data 读取列表字段，用 factory 转换每个 dict 元素。空或无效返回 None。"""
    raw = pdata.get(key)
    if not isinstance(raw, list) or not raw:
        return None
    built = [factory(item) for item in raw if isinstance(item, dict)]
    return built or None


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
    result = await db.execute(select(UserProfile).where(UserProfile.user_id == user_id))
    profile = result.scalar_one_or_none()

    if profile is None:
        profile = UserProfile(user_id=user_id)
        db.add(profile)

    # 映射字段 — 仅当有值时才覆盖，防止空数据清空已有画像
    _set_if_exists(profile, "school_name", data.get("school_name"))
    _set_if_exists(profile, "school_level", data.get("school_level"))
    _set_if_exists(profile, "major", data.get("major"))
    _set_if_exists(profile, "grade", _map_grade(data.get("grade")))
    _set_if_exists(profile, "graduation_year", data.get("graduation_year"))
    _set_if_exists(profile, "target_direction", _map_direction(data.get("target_direction")))
    _set_if_exists(profile, "target_company_level", data.get("target_company_level"))

    skills_context: dict[str, str] = {}
    skills = data.get("current_skills")
    if isinstance(skills, list):
        if not skills:
            # 空列表：清空旧技能
            profile.current_skills = []
        else:
            # 标准化为 [{"skill": name, "level": level}, ...]
            normalized = []
            for s in skills:
                if isinstance(s, str):
                    normalized.append({"skill": s, "level": _DEFAULT_SKILL_LEVEL})
                elif isinstance(s, dict):
                    name = s.get("name") or s.get("skill") or ""
                    normalized.append(
                        {
                            "skill": name,
                            "level": s.get("level", _DEFAULT_SKILL_LEVEL),
                        }
                    )
                    ctx = s.get("context")
                    if name and ctx:
                        skills_context[name] = ctx
            profile.current_skills = normalized

    # 双写：扩展画像数据 → profile_data JSON,不动 ORM 列
    pdata = dict(profile.profile_data or {})
    edu = data.get("education") if isinstance(data.get("education"), dict) else None
    if edu:
        edu_clean: dict = {}
        if edu.get("gpa"):
            edu_clean["gpa"] = edu["gpa"]
        if edu.get("ranking"):
            edu_clean["ranking"] = edu["ranking"]
        awards = edu.get("awards")
        if isinstance(awards, list) and awards:
            edu_clean["awards"] = [str(a) for a in awards if a]
        if edu_clean:
            pdata["education"] = {**(pdata.get("education") or {}), **edu_clean}
    if skills_context:
        pdata["skills_context"] = {**(pdata.get("skills_context") or {}), **skills_context}

    # ── 扩展字段：直接存入 profile_data，不映射、不转换 ──
    for key in ("bio", "city", "english_level", "expected_salary"):
        _set_ext(pdata, data, key)
    _set_ext(pdata, data, "work_experience", default=[])
    _set_ext(pdata, data, "projects", default=[])
    _set_ext(pdata, data, "portfolio_links", default=[])

    profile.profile_data = pdata

    await db.flush()
    return _profile_to_response(profile, data.get("name"))


def _map_grade(grade: str | None) -> str | None:
    """标准化年级值"""
    if not grade:
        return None
    valid = {
        "freshman",
        "sophomore",
        "junior",
        "senior",
        "graduate1",
        "graduate2",
        "graduate3",
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
    result = await db.execute(select(UserProfile).where(UserProfile.user_id == user_id))
    profile = result.scalar_one_or_none()

    # 同时查 nickname
    user = await db.get(User, user_id)
    nickname = user.nickname if user else None

    return _profile_to_response(profile, nickname) if profile else ProfileResponse(nickname=nickname)


async def update_profile(db: AsyncSession, user_id: str, patch: ProfileUpdate) -> ProfileResponse:
    """局部更新用户画像"""
    result = await db.execute(select(UserProfile).where(UserProfile.user_id == user_id))
    profile = result.scalar_one_or_none()

    if profile is None:
        profile = UserProfile(user_id=user_id)
        db.add(profile)

    set_fields = patch.model_fields_set
    indirect = {
        "current_skills",
        "nickname",
        "gpa",
        "ranking",
        "awards",
        "bio",
        "city",
        "english_level",
        "expected_salary",
        "portfolio_links",
        "projects",
        "work_experience",
    }
    patch_data = patch.model_dump(exclude_unset=True, exclude=indirect)

    for key, value in patch_data.items():
        setattr(profile, key, value)

    pdata = dict(profile.profile_data or {})

    if "current_skills" in set_fields:
        skills = patch.current_skills or []
        profile.current_skills = [{"skill": s.name, "level": s.level} for s in skills]
        existing_ctx = pdata.get("skills_context") or {}
        new_ctx = {s.name: s.context for s in skills if s.context}
        merged = {**existing_ctx, **new_ctx}
        for s in skills:
            if not s.context:
                merged.pop(s.name, None)
        pdata["skills_context"] = merged if merged else None

    edu_keys = {"gpa", "ranking", "awards"} & set_fields
    if edu_keys:
        edu = dict(pdata.get("education") or {})
        for k in edu_keys:
            v = getattr(patch, k)
            if v is None or (k == "awards" and not v):
                edu.pop(k, None)
            else:
                edu[k] = v
        if edu:
            pdata["education"] = edu
        else:
            pdata.pop("education", None)

    # ── 扩展字段：直接写入 profile_data ──
    for key in ("bio", "city", "english_level", "expected_salary"):
        if key in set_fields:
            v = getattr(patch, key)
            if v:
                pdata[key] = v
            else:
                pdata.pop(key, None)

    _list_serializers = {
        "portfolio_links": lambda p: {"label": p.label, "url": p.url},
        "projects": lambda p: {
            "title": p.title,
            "tech_stack": p.tech_stack,
            "role": p.role,
            "period": p.period,
            "description": p.description,
        },
        "work_experience": lambda w: {
            "company": w.company,
            "role": w.role,
            "period": w.period,
            "description": w.description,
        },
    }
    for key, serializer in _list_serializers.items():
        if key not in set_fields:
            continue
        items = getattr(patch, key)
        if items:
            pdata[key] = [serializer(i) for i in items]
        else:
            pdata.pop(key, None)

    profile.profile_data = pdata

    if "nickname" in set_fields:
        user = await db.get(User, user_id)
        if user:
            user.nickname = patch.nickname

    await db.flush()
    user = await db.get(User, user_id)
    return _profile_to_response(profile, user.nickname if user else None)


async def reset_profile(db: AsyncSession, user_id: str) -> ProfileResponse:
    """清空用户画像 — 删 UserProfile 行,保留 User 行与 nickname"""
    await db.execute(delete(UserProfile).where(UserProfile.user_id == user_id))
    await db.flush()
    user = await db.get(User, user_id)
    return ProfileResponse(nickname=user.nickname if user else None)


def _profile_to_response(profile: UserProfile | None, nickname: str | None) -> ProfileResponse:
    """ORM → 响应模型"""
    if profile is None:
        return ProfileResponse(nickname=nickname)

    pdata = profile.profile_data or {}
    skills_ctx = pdata.get("skills_context") or {}
    edu = pdata.get("education") or {}

    raw_skills = profile.current_skills or []
    skills = []
    if isinstance(raw_skills, list):
        for s in raw_skills:
            if isinstance(s, dict):
                name = s.get("skill") or s.get("name") or ""
                skills.append(
                    SkillItem(
                        name=name,
                        level=s.get("level", _DEFAULT_SKILL_LEVEL),
                        context=skills_ctx.get(name) if name else None,
                    )
                )

    awards = edu.get("awards") if isinstance(edu.get("awards"), list) else None

    # ── 扩展字段：直接从 profile_data 读，不做任何字段名映射 ──
    portfolio_links = _build_list_from_pdata(
        pdata, "portfolio_links", lambda p: PortfolioLink(label=p.get("label", ""), url=p.get("url", ""))
    )
    projects = _build_list_from_pdata(
        pdata,
        "projects",
        lambda p: ProjectItem(
            title=p.get("title", ""),
            tech_stack=p.get("tech_stack"),
            role=p.get("role"),
            period=p.get("period", ""),
            description=p.get("description", ""),
        ),
    )
    work_experience = _build_list_from_pdata(
        pdata,
        "work_experience",
        lambda w: WorkExperienceItem(
            company=w.get("company", ""),
            role=w.get("role", ""),
            period=w.get("period", ""),
            description=w.get("description", ""),
        ),
    )

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
        gpa=edu.get("gpa"),
        ranking=edu.get("ranking"),
        awards=awards if awards else None,
        bio=pdata.get("bio"),
        city=pdata.get("city"),
        english_level=pdata.get("english_level"),
        expected_salary=pdata.get("expected_salary"),
        portfolio_links=portfolio_links if portfolio_links else None,
        projects=projects if projects else None,
        work_experience=work_experience if work_experience else None,
    )


# ═══════════════════════════════════════════════
# 主流程：上传 → 提取 → 解析 → 保存
# ═══════════════════════════════════════════════


async def process_resume(db: AsyncSession, user_id: str, file: UploadFile) -> ResumeUploadResponse:
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
    preview = raw_text[:_PREVIEW_LENGTH].replace("\n", " ")
    return ResumeUploadResponse(profile=profile, raw_text_preview=preview)
