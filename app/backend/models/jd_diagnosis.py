"""JD 诊断历史 ORM — 单次「用户画像 vs JD」的诊断结果落库。

每次调用诊断接口会在本表插入一行，result_data 存结构化 JSON（缺口、优势、风险、
简历建议、行动计划等）。岗位追踪卡片通过 diagnosis_id 指向其中一行，实现
「一张卡片 ↔ 一次诊断快照」。
"""

import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, func, JSON, Text, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.backend.db.base import Base


class JDDiagnosis(Base):
    """单条 JD 诊断记录；可被多个 JobTarget 引用（一般一对一）。"""

    __tablename__ = "jd_diagnoses"

    diagnosis_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.user_id"), index=True
    )

    jd_text: Mapped[str] = mapped_column(
        Text
    )  # 参与诊断的原始 JD 文本；保留便于重跑诊断或审计
    jd_title: Mapped[str | None] = mapped_column(
        String(200)
    )  # 展示用标题；LLM 或规则提取失败时可兜底为「未命名 JD」

    overall_score: Mapped[int] = mapped_column(
        Integer, default=0
    )  # 综合匹配分 0–100；看板卡片上的 match_score 通常与此一致

    summary: Mapped[str | None] = mapped_column(
        Text
    )  # 诊断摘要段落，供详情页与 LLM 建议 prompt 摘要使用

    # 与 ORM 列并存的扩展字段：skill_gaps、matched_skills、strengths、risks、resume_tips、action_plan 等
    result_data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def get_skill_gaps(self) -> list[dict]:
        """从 result_data 中安全读取 skill_gaps 列表。

        每项一般为 {"skill": str, "priority": str}；业务层应用本方法而非直接
        result_data["skill_gaps"]，避免键缺失或类型异常。
        """
        return self.result_data.get("skill_gaps", []) if self.result_data else []
