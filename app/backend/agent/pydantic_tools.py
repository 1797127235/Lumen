"""PydanticAI Agent 工具 — 使用 @agent.tool 装饰器

工具列表：
- get_profile: 读取用户画像
- update_profile: 从对话中增量更新画像
- diagnose_jd: JD 对比分析
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic_ai import Agent, RunContext

from app.backend.agent.deps import CareerOSDeps

logger = logging.getLogger(__name__)


def register_tools(agent: Agent[CareerOSDeps, str]) -> None:
    """注册所有工具到 Agent

    Args:
        agent: PydanticAI Agent 实例
    """

    @agent.tool
    async def get_profile(ctx: RunContext[CareerOSDeps]) -> str:
        """读取用户画像。仅当用户明确要求「查看我的画像」「我的信息」时调用，不要主动调用。"""
        logger.info("工具调用: get_profile, user_id=%s", ctx.deps.user_id)
        from sqlalchemy import select

        from app.backend.models.user import User, UserProfile

        db = ctx.deps.db
        user_id = ctx.deps.user_id

        # 查询画像
        result = await db.execute(select(UserProfile).where(UserProfile.user_id == user_id))
        profile = result.scalar_one_or_none()

        if not profile:
            return "用户画像为空，请先上传简历或手动填写画像。"

        # 查询昵称
        user = await db.get(User, user_id)
        nickname = user.nickname if user else None

        # 组装画像摘要
        parts = []
        if nickname:
            parts.append(f"姓名：{nickname}")
        if profile.school_name:
            parts.append(f"学校：{profile.school_name}（{profile.school_level or '未知'}）")
        if profile.major:
            parts.append(f"专业：{profile.major}")
        if profile.grade:
            grade_map = {
                "freshman": "大一",
                "sophomore": "大二",
                "junior": "大三",
                "senior": "大四",
                "graduate1": "研一",
                "graduate2": "研二",
                "graduate3": "研三",
            }
            parts.append(f"年级：{grade_map.get(profile.grade, profile.grade)}")
        if profile.target_direction:
            parts.append(f"目标方向：{profile.target_direction}")
        if profile.target_company_level:
            level_map = {"top": "大厂", "major": "中厂", "medium": "小厂", "state_owned": "国企"}
            parts.append(f"目标公司：{level_map.get(profile.target_company_level, profile.target_company_level)}")

        skills = profile.current_skills
        if skills and isinstance(skills, list):
            skill_names = [s.get("skill", s.get("name", "")) for s in skills if isinstance(s, dict)]
            if skill_names:
                parts.append(f"技能：{', '.join(skill_names)}")

        # 扩展字段
        pdata = profile.profile_data or {}
        if pdata.get("bio"):
            parts.append(f"简介：{pdata['bio']}")
        if pdata.get("education"):
            edu = pdata["education"]
            if edu.get("gpa"):
                parts.append(f"GPA：{edu['gpa']}")
            if edu.get("awards"):
                parts.append(f"获奖：{', '.join(edu['awards'])}")

        return "\n".join(parts) if parts else "画像数据不完整，请补充信息。"

    @agent.tool
    async def update_profile(
        ctx: RunContext[CareerOSDeps],
        fields: dict[str, Any],
    ) -> str:
        """更新用户画像。当用户提到以下信息时【必须】调用：
        - 学校、专业、年级（如"我是大三的"、"软件工程专业"）
        - 目标方向（如"想做AI Agent"、"后端开发"）
        - 目标公司（如"想去大厂"、"国企"）
        - 个人简介、城市、薪资期望等

        Args:
            fields: 要更新的字段字典，支持的字段：
                - school_name: 学校名称
                - major: 专业
                - grade: 年级（freshman/sophomore/junior/senior/graduate1/graduate2/graduate3）
                - target_direction: 目标方向（后端/前端/算法/AI等）
                - target_company_level: 目标公司（top/major/medium/state_owned）
                - bio: 个人简介
                - city: 城市
                - expected_salary: 期望薪资
                - english_level: 英语水平
        """
        logger.info("工具调用: update_profile, user_id=%s, fields=%s", ctx.deps.user_id, fields)
        from sqlalchemy import select

        from app.backend.models.user import UserProfile
        from app.backend.services.profile_service import _map_direction

        db = ctx.deps.db
        user_id = ctx.deps.user_id

        # 定义允许的字段
        allowed_fields = {
            "school_name",
            "major",
            "grade",  # 基础信息
            "target_direction",
            "target_company_level",  # 目标
            "bio",
            "city",
            "expected_salary",
            "english_level",  # 扩展信息
        }

        # 过滤掉未知字段
        unknown_fields = set(fields.keys()) - allowed_fields
        if unknown_fields:
            logger.warning("忽略未知字段: %s", unknown_fields)
            fields = {k: v for k, v in fields.items() if k in allowed_fields}

        result = await db.execute(select(UserProfile).where(UserProfile.user_id == user_id))
        profile = result.scalar_one_or_none()

        if not profile:
            profile = UserProfile(user_id=user_id)
            db.add(profile)
            await db.flush()

        pdata = dict(profile.profile_data or {})
        updated_fields = []

        # 直接映射的字段（存到 profile 表）
        direct_fields = {"school_name", "major"}
        for key in direct_fields:
            if key in fields and fields[key] is not None:
                setattr(profile, key, fields[key])
                updated_fields.append(key)

        # grade 字段需要校验合法值
        valid_grades = {"freshman", "sophomore", "junior", "senior", "graduate1", "graduate2", "graduate3"}
        if "grade" in fields and fields["grade"] is not None:
            grade_val = fields["grade"].lower() if isinstance(fields["grade"], str) else fields["grade"]
            # 支持中文年级映射
            grade_map = {
                "大一": "freshman",
                "大二": "sophomore",
                "大三": "junior",
                "大四": "senior",
                "研一": "graduate1",
                "研二": "graduate2",
                "研三": "graduate3",
            }
            grade_val = grade_map.get(grade_val, grade_val)
            if grade_val in valid_grades:
                profile.grade = grade_val
                updated_fields.append("grade")
            else:
                logger.warning("无效的 grade: %s", fields["grade"])

        # target_direction 需要校验合法值
        if "target_direction" in fields and fields["target_direction"] is not None:
            mapped = _map_direction(fields["target_direction"])
            if mapped:
                profile.target_direction = mapped
                updated_fields.append("target_direction")
            else:
                logger.warning("无效的 target_direction: %s", fields["target_direction"])

        # target_company_level 需要校验合法值
        valid_company_levels = {"top", "major", "medium", "state_owned"}
        if "target_company_level" in fields and fields["target_company_level"] is not None:
            if fields["target_company_level"] in valid_company_levels:
                profile.target_company_level = fields["target_company_level"]
                updated_fields.append("target_company_level")
            else:
                logger.warning("无效的 target_company_level: %s", fields["target_company_level"])

        # 扩展字段存入 profile_data
        ext_fields = {"bio", "city", "expected_salary", "english_level"}
        for key in ext_fields:
            if key in fields and fields[key] is not None:
                pdata[key] = fields[key]
                updated_fields.append(key)

        profile.profile_data = pdata
        await db.flush()

        if updated_fields:
            return f"画像已更新：{', '.join(updated_fields)}"
        else:
            return "没有需要更新的字段。"
