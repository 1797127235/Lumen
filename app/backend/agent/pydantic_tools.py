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
        """读取用户画像，包括学校、专业、技能、目标方向等信息。当需要了解用户背景时调用。"""
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
        """从对话中增量更新用户画像。当用户提到目标方向、目标公司、个人偏好等信息时调用。

        Args:
            fields: 要更新的字段字典，支持的字段：
                - target_direction: 目标方向（后端/前端/算法/AI等）
                - target_company_level: 目标公司（top/major/medium/state_owned）
                - bio: 个人简介
                - city: 城市
                - expected_salary: 期望薪资
                - english_level: 英语水平
        """
        from sqlalchemy import select

        from app.backend.models.user import UserProfile
        from app.backend.services.profile_service import _map_direction

        db = ctx.deps.db
        user_id = ctx.deps.user_id

        # 定义允许的字段
        allowed_fields = {"target_direction", "target_company_level", "bio", "city", "expected_salary", "english_level"}

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

    @agent.tool
    async def diagnose_jd(
        ctx: RunContext[CareerOSDeps],
        jd_text: str,
    ) -> str:
        """诊断用户与 JD 的匹配度。当用户粘贴 JD 或询问岗位匹配情况时调用。

        Args:
            jd_text: JD 岗位描述文本
        """
        from app.backend.services.jd_service import diagnose_jd

        db = ctx.deps.db
        user_id = ctx.deps.user_id

        try:
            result = await diagnose_jd(db, user_id, jd_text)

            # 格式化输出
            parts = [
                "【JD 诊断结果】",
                f"岗位：{result.jd_title}",
                f"匹配度：{result.overall_score}/100",
                f"总结：{result.summary}",
            ]

            if result.matched_skills:
                parts.append(f"匹配技能：{', '.join(result.matched_skills)}")

            if result.skill_gaps:
                gaps = [f"{g.skill}（{g.priority}）" for g in result.skill_gaps]
                parts.append(f"技能缺口：{', '.join(gaps)}")

            if result.strengths:
                parts.append(f"优势：{', '.join(result.strengths)}")

            if result.risks:
                parts.append(f"风险：{', '.join(result.risks)}")

            if result.action_plan:
                parts.append("行动计划：")
                for i, plan in enumerate(result.action_plan, 1):
                    parts.append(f"  {i}. {plan}")

            return "\n".join(parts)
        except Exception as e:
            logger.error("JD 诊断失败: %s", e, exc_info=True)
            # 不向用户暴露内部错误细节
            return "JD 诊断失败，请稍后重试。如果问题持续存在，请联系管理员。"

    logger.info("Tools registered: get_profile, update_profile, diagnose_jd")
