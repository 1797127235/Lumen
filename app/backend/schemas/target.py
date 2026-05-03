"""岗位追踪 API 的 Pydantic 模型 — 请求体验证与响应序列化。

与 ORM JobTarget 对应：创建/更新用入参模型，看板与详情用出参模型。
状态合法值在 VALID_STATUSES 中集中定义，与数据库中存英文字符串一致。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

# 看板列与 PATCH 可写入的 status 白名单（扩展新状态时需同步前端列配置）
VALID_STATUSES = Literal["interested", "applied", "test", "interview", "offer", "rejected", "abandoned"]


class TargetCreate(BaseModel):
    """POST 创建岗位时的请求体。

    三种入参组合：
    - 传 diagnosis_id：复用已有诊断（从 JD 报告页"加入看板"过来），后端不再调 LLM。
    - 传 jd_text：服务端同步调用 JD 诊断并写入 diagnosis_id / match_score。
    - 都不传：仅建卡，无匹配分，仍会异步生成 agent_advice（用精简 prompt）。

    diagnosis_id 与 jd_text 互斥；同时传会被 model_validator 拒掉（422）。
    """

    company: str = Field(..., min_length=1, max_length=100)
    title: str = Field(..., min_length=1, max_length=100)
    location: str | None = Field(default=None, max_length=50)
    salary: str | None = Field(default=None, max_length=50)
    jd_text: str | None = None
    jd_url: str | None = Field(default=None, max_length=500)
    diagnosis_id: str | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _check_diagnosis_source(self) -> TargetCreate:
        if self.diagnosis_id and self.jd_text:
            raise ValueError("diagnosis_id 和 jd_text 不能同时传")
        return self


class TargetUpdate(BaseModel):
    """PATCH 部分更新；未传的字段表示「不修改」。

    status 变更时：后端会把卡片移到目标列末尾（重算 sort_order），并可能
    触发异步重新生成 agent_advice（见 target_service.update_target）。
    """

    company: str | None = Field(default=None, min_length=1, max_length=100)
    title: str | None = Field(default=None, min_length=1, max_length=100)
    status: VALID_STATUSES | None = None
    interview_round: str | None = Field(default=None, max_length=50)
    location: str | None = Field(default=None, max_length=50)
    salary: str | None = Field(default=None, max_length=50)
    notes: str | None = None


class TargetCard(BaseModel):
    """看板单列中一张卡片所需的最小字段（列表接口/嵌套在 columns 中）。"""

    target_id: str
    company: str
    title: str
    status: str
    interview_round: str | None = None
    match_score: int | None = None  # 无诊断时为 None，前端可不显示百分比
    agent_advice: str | None = None  # 异步生成完成前可能为 None
    location: str | None = None
    created_at: str  # ISO8601 字符串，便于前端直接展示


class TargetDetail(TargetCard):
    """详情页：在卡片基础上附带 JD 原文、链接、备注及完整诊断结构。"""

    jd_text: str | None = None
    jd_url: str | None = None
    salary: str | None = None
    notes: str | None = None
    diagnosis: dict | None = None  # 与 JDDiagnoseResponse.model_dump() 对齐的扁平 dict


class BoardStats(BaseModel):
    """看板顶部统计条：总量、平均分、高频技能缺口 TOP。"""

    total: int
    avg_score: float  # 仅统计有 match_score 的卡；全无则 0
    common_gaps: list[str]  # 多卡诊断 skill_gaps 聚合后的展示用标签列表


class BoardResponse(BaseModel):
    """GET 看板整页：按 status 分组的列数据 + 全局统计。"""

    columns: dict[str, list[TargetCard]]  # key 为 status 英文值，value 为该列卡片数组
    stats: BoardStats
