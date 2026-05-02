"""JD 诊断 API 请求/响应模型"""

from __future__ import annotations

from pydantic import BaseModel, Field


class JDDiagnoseRequest(BaseModel):
    jd_text: str = Field(..., min_length=10, max_length=10000, description="岗位描述全文")


class GapSkill(BaseModel):
    skill: str
    priority: str = "medium"  # high / medium / low


class JDDiagnoseResponse(BaseModel):
    diagnosis_id: str | None = None  # 存库后返回
    jd_text: str | None = None  # 原始 JD 文本，支持重诊断
    jd_title: str = ""
    overall_score: int = Field(default=0, ge=0, le=100)
    summary: str = ""
    skill_gaps: list[GapSkill] = []
    matched_skills: list[str] = []
    strengths: list[str] = []
    risks: list[str] = []
    resume_tips: list[str] = []
    action_plan: list[str] = []
