"""岗位追踪卡片 ORM — 求职看板中的一条岗位记录。

每张卡片对应用户正在关注或推进的一个目标岗位，可与 JD 诊断（jd_diagnoses）关联，
用于展示匹配分、缺口聚合以及 LLM 生成的一句话行动建议。

设计要点参见：docs/功能设计/岗位追踪看板.md
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.backend.db.base import Base


class JobTarget(Base):
    """用户维度的单条岗位追踪记录（看板卡片）。"""

    __tablename__ = "job_targets"

    # ── 主键与归属 ─────────────────────────────────────
    target_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.user_id"), index=True)
    # 建索引：按 user_id 拉取整板数据是最常见查询

    # ── 岗位展示信息（用户填写或从 JD 摘要）────────────
    company: Mapped[str] = mapped_column(String(100))  # 公司名，看板卡片主标题之一
    title: Mapped[str] = mapped_column(String(100))  # 岗位名称
    location: Mapped[str | None] = mapped_column(String(50))  # 工作城市，可选
    salary: Mapped[str | None] = mapped_column(String(50))  # 薪资范围文案，可选（如 30-50K）
    jd_text: Mapped[str | None] = mapped_column(Text)  # JD 全文；有则创建时会触发诊断
    jd_url: Mapped[str | None] = mapped_column(String(500))  # 官方招聘页等外链，可选

    # ── 看板状态 ───────────────────────────────────────
    # 取值与前端列对应：interested / applied / test / interview / offer / rejected / abandoned
    status: Mapped[str] = mapped_column(String(20), default="interested")
    interview_round: Mapped[str | None] = mapped_column(
        String(50)
    )  # 仅 status=interview 时有意义；自由文本，如「二面」「HR 面」

    # ── 与 JD 诊断的关联 ───────────────────────────────
    diagnosis_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("jd_diagnoses.diagnosis_id", ondelete="SET NULL"),
    )  # 诊断记录删除后外键置空，卡片保留；详情页可提示「无诊断」
    match_score: Mapped[int | None] = mapped_column(
        Integer
    )  # 冗余存储 overall_score，避免看板列表每条都 JOIN；与诊断不一致时以诊断为准可后续校准

    # ── 智能体输出与用户备注 ───────────────────────────
    agent_advice: Mapped[str | None] = mapped_column(
        Text
    )  # LLM 生成的一句话建议；由 BackgroundTasks 异步写入，首屏可能为空
    notes: Mapped[str | None] = mapped_column(Text)  # 用户自由备注，如内推人、投递渠道

    # ── 同列排序（拖拽落列末尾策略）────────────────────
    sort_order: Mapped[int] = mapped_column(
        Integer, default=0
    )  # 同一 status 下数值越小越靠前（或按产品约定）；PATCH 改列时重置为该列 max+1

    # ── 时间戳 ─────────────────────────────────────────
    # 仅当 status 变更时由业务代码更新；避免把「只改 notes」当成状态时间线
    status_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )  # 任意字段更新都会刷新；若要做「距上次改状态」应用 status_changed_at
