"""岗位追踪业务层 — 看板 CRUD、与 JD 诊断联动、LLM 行动建议。

职责划分：
- create_target / get_board / get_target / update_target / delete_target：在调用方
  传入的 AsyncSession 内执行，由 FastAPI get_db 负责提交或回滚。
- generate_advice：供 BackgroundTasks 调用；内部自开新 session 并 commit，
  因为 HTTP 返回后原请求里的 session 可能已关闭。
建议生成策略：无论是否绑定诊断都会生成。有诊断时 prompt 含匹配分与 TOP 缺口，
无诊断时仅依据画像、岗位、状态、距创建天数。两套 prompt 由 _call_advice_llm 切换。
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.backend.agent.llm_router import chat as llm_chat
from app.backend.db.base import get_async_session_maker
from app.backend.models.jd_diagnosis import JDDiagnosis
from app.backend.models.job_target import JobTarget
from app.backend.schemas.jd import GapSkill, JDDiagnoseResponse
from app.backend.schemas.target import (
    BoardResponse,
    BoardStats,
    TargetCard,
    TargetCreate,
    TargetDetail,
    TargetUpdate,
)
from app.backend.services.jd_service import _load_profile_summary, diagnose_jd

logger = logging.getLogger(__name__)

# status 数据库英文值 → 写入 LLM prompt 的中文列名（用户可读）
_STATUS_LABELS: dict[str, str] = {
    "interested": "感兴趣",
    "applied": "已投递",
    "test": "笔试",
    "interview": "面试",
    "offer": "Offer",
    "rejected": "被拒",
    "abandoned": "放弃",
}

# LLM 失败或返回空时写入 DB，避免前端长期显示「生成中」
_ADVICE_FALLBACK = "AI 建议暂未生成，可稍后点击「重新生成建议」重试"

# 与 llm_router 的 general_chat 搭配：温度略低、输出短，降低跑题概率
_ADVICE_PROMPT_WITH_DIAGNOSIS = """你是求职陪跑教练。基于以下信息，给一句话行动建议（≤50字）。

【用户画像】
{profile_summary}

【岗位】{company} - {title}
【当前状态】{status_label}
【距创建】{days_since_create}天
【诊断要点】匹配 {match_score}/100，主要缺口：{top_gaps}

只输出建议本身，不要解释，不要前缀。"""

# 无诊断时使用：去掉匹配分和缺口字段，免得模型编造数字
_ADVICE_PROMPT_NO_DIAGNOSIS = """你是求职陪跑教练。基于以下信息，给一句话行动建议（≤50字）。
【用户画像】
{profile_summary}
【岗位】{company} - {title}
【当前状态】{status_label}
【距创建】{days_since_create}天

只输出建议本身，不要解释，不要前缀。"""


# ── CRUD ──────────────────────────────────────────────
async def create_target(db: AsyncSession, user_id: str, req: TargetCreate) -> TargetDetail:
    """新增一条岗位卡片，初始落在「感兴趣」列。
    三种入参组合（schemas/target.py 已校验互斥）：
    - 路径 A — 传 diagnosis_id：从既有诊断复用 jd_text/match_score，不再调 LLM；
      诊断不存在或不属于当前 user_id 时抛 404。
    - 路径 B — 传 jd_text：同步调用 diagnose_jd 写诊断并关联本卡。
    - 路径 C — 都不传：仅建卡，diagnosis_id/match_score 为空。
    注意：agent_advice 不在此函数内生成，由路由 BackgroundTasks 异步补全。
    """

    diagnosis_id: str | None = None
    match_score: int | None = None
    diagnosis_dict: dict | None = None
    jd_text = req.jd_text  # 路径 A 会被诊断里的 jd_text 覆盖

    if req.diagnosis_id:
        # 路径 A：复用已有诊断
        diagnosis = await db.get(JDDiagnosis, req.diagnosis_id)
        if not diagnosis or diagnosis.user_id != user_id:
            raise HTTPException(status_code=404, detail="诊断不存在或无权限")
        diagnosis_id = diagnosis.diagnosis_id
        match_score = diagnosis.overall_score
        diagnosis_dict = _diagnosis_to_dict(diagnosis)
        jd_text = diagnosis.jd_text  # 从诊断复制 JD 原文，避免详情页 jd_text 为空
    elif req.jd_text and req.jd_text.strip():
        # 路径 B：同步诊断
        diag = await diagnose_jd(db, user_id, req.jd_text)
        diagnosis_id = diag.diagnosis_id
        match_score = diag.overall_score
        diagnosis_dict = diag.model_dump()
    # 路径 C：什么都不做，三个变量保持 None

    # 新卡一律进 interested 列，排序键取该列当前尾部
    sort_order = await _next_sort_order(db, user_id, "interested")

    target = JobTarget(
        user_id=user_id,
        company=req.company,
        title=req.title,
        location=req.location,
        salary=req.salary,
        jd_text=jd_text,
        jd_url=req.jd_url,
        status="interested",
        diagnosis_id=diagnosis_id,
        match_score=match_score,
        notes=req.notes,
        sort_order=sort_order,
    )
    db.add(target)
    await db.flush()
    logger.info(
        "岗位创建: target_id=%s user_id=%s match_score=%s",
        target.target_id,
        user_id,
        match_score,
    )
    return _to_detail(target, diagnosis_dict)


async def get_board(db: AsyncSession, user_id: str) -> BoardResponse:
    """拉取某用户全部岗位，按 status 分桶并计算看板顶部统计。

    columns：key 为合法 status，缺失状态对应空列表（前端可固定列顺序渲染）。
    stats.avg_score：仅对 match_score 非空的卡求平均；无则 0。
    stats.common_gaps：跨卡关联诊断，对 skill_gaps 做频次 TOP5（归一化 skill 名）。
    """

    targets = (
        (
            await db.execute(
                select(JobTarget)
                .where(JobTarget.user_id == user_id)
                .order_by(JobTarget.sort_order.asc(), JobTarget.created_at.asc())
            )
        )
        .scalars()
        .all()
    )

    # 先为已知列占位，避免前端取 columns['interested'] KeyError
    columns: dict[str, list[TargetCard]] = {key: [] for key in _STATUS_LABELS}
    scored: list[int] = []
    diagnosis_ids: list[str] = []
    for t in targets:
        # 若历史数据含未知 status，动态加一列以免丢卡
        if t.status not in columns:
            columns.setdefault(t.status, [])
        columns[t.status].append(_to_card(t))
        if t.match_score is not None:
            scored.append(t.match_score)
        if t.diagnosis_id:
            diagnosis_ids.append(t.diagnosis_id)

    avg_score = round(sum(scored) / len(scored), 1) if scored else 0.0
    common_gaps = await _aggregate_gaps(db, diagnosis_ids)

    return BoardResponse(
        columns=columns,
        stats=BoardStats(
            total=len(targets),
            avg_score=avg_score,
            common_gaps=common_gaps,
        ),
    )


async def get_target(db: AsyncSession, user_id: str, target_id: str) -> TargetDetail:
    """单卡详情：含 JD 字段与 diagnosis 字典（由 JDDiagnoseResponse 形状组装）。

    404：target_id 不存在或不属于该 user_id（防水平越权）。
    """

    target = await _load_target(db, user_id, target_id)
    diagnosis_dict = await _load_diagnosis_dict(db, target.diagnosis_id)
    return _to_detail(target, diagnosis_dict)


async def update_target(
    db: AsyncSession, user_id: str, target_id: str, patch: TargetUpdate
) -> tuple[TargetDetail, bool]:
    """部分更新岗位。返回 (详情, needs_advice)。

    needs_advice 为 True 时，路由应追加 BackgroundTasks.generate_advice：
    只要状态变更就重新生成（无诊断也走精简 prompt）。
    状态变更会重算目标列 sort_order，并刷新 status_changed_at 供后续时间线扩展。
    """

    target = await _load_target(db, user_id, target_id)

    status_changed = patch.status is not None and patch.status != target.status

    # fields_set 区分「缺失」与「显式 null」：前端发 null 想清空时，is not None 会吞掉
    fields_set = patch.model_fields_set

    if "company" in fields_set:
        target.company = patch.company
    if "title" in fields_set:
        target.title = patch.title
    if "interview_round" in fields_set:
        target.interview_round = patch.interview_round
    if "location" in fields_set:
        target.location = patch.location
    if "salary" in fields_set:
        target.salary = patch.salary
    if "notes" in fields_set:
        target.notes = patch.notes

    if status_changed:
        target.status = patch.status  # type: ignore[assignment]
        # V1 策略：拖到新列一律追加到该列末尾
        target.sort_order = await _next_sort_order(db, user_id, target.status)
        target.status_changed_at = datetime.now(UTC)

    await db.flush()

    diagnosis_dict = await _load_diagnosis_dict(db, target.diagnosis_id)
    return _to_detail(target, diagnosis_dict), status_changed


async def delete_target(db: AsyncSession, user_id: str, target_id: str) -> bool:
    """物理删除岗位卡片；不级联删除 jd_diagnoses（诊断可被历史或其它引用保留）。"""

    target = await _load_target(db, user_id, target_id)
    await db.delete(target)
    await db.flush()
    return True


# ── BackgroundTasks ───────────────────────────────────


async def generate_advice(target_id: str, user_id: str) -> None:
    """异步任务入口：生成并写回 agent_advice。

    必须在 BackgroundTasks 中调用：本函数自行 commit，且使用独立 session。
    无诊断的卡片也会生成（走精简 prompt）；LLM 失败时落兜底文案，避免 UI 永久卡在「生成中」。
    """
    session_maker = get_async_session_maker()
    async with session_maker() as db:
        target = (
            await db.execute(
                select(JobTarget).where(
                    JobTarget.target_id == target_id,
                    JobTarget.user_id == user_id,
                )
            )
        ).scalar_one_or_none()

        if not target:
            return

        try:
            diagnosis: JDDiagnosis | None = None
            if target.diagnosis_id:
                diagnosis = (
                    await db.execute(select(JDDiagnosis).where(JDDiagnosis.diagnosis_id == target.diagnosis_id))
                ).scalar_one_or_none()
                # 诊断行丢失也不阻塞建议生成，diagnosis 留 None 走精简分支

            profile_summary = await _load_profile_summary(db, user_id)
            advice = await _call_advice_llm(target, diagnosis, profile_summary)
            if advice:
                target.agent_advice = advice
                await db.commit()
                logger.info(
                    "agent_advice 已生成: target_id=%s len=%d",
                    target_id,
                    len(advice),
                )
            else:
                target.agent_advice = _ADVICE_FALLBACK
                await db.commit()
                logger.warning(
                    "agent_advice LLM 返回空: target_id=%s，已写入兜底",
                    target_id,
                )
        except Exception:
            # 兜底：LLM 超时/异常时写入提示文案，UI 不再卡在「建议生成中」
            await db.rollback()
            logger.exception("agent_advice 生成失败: target_id=%s", target_id)
            try:
                target.agent_advice = _ADVICE_FALLBACK
                await db.commit()
            except Exception:
                await db.rollback()
                logger.exception("agent_advice 兜底落库也失败: target_id=%s", target_id)


async def _call_advice_llm(
    target: JobTarget,
    diagnosis: JDDiagnosis | None,
    profile_summary: str,
) -> str:
    """组装求职教练 prompt，调用通用对话模型，返回去首尾空白的建议正文。

    有诊断时：用 _ADVICE_PROMPT_WITH_DIAGNOSIS，含匹配分与 TOP 缺口。
    无诊断时：用 _ADVICE_PROMPT_NO_DIAGNOSIS，仅画像 + 岗位 + 状态 + 距创建天数。
    缺口展示：优先 high priority 技能名，不足再用其它优先级凑满 3 个；无缺口时写「无」。
    """

    now = datetime.now(UTC)
    created = target.created_at
    if created and created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    days_since_create = (now - created).days if created else 0

    status_label = _STATUS_LABELS.get(target.status, target.status)
    if target.status == "interview" and target.interview_round:
        status_label = f"{status_label}（{target.interview_round}）"

    if diagnosis is not None:
        gaps = diagnosis.get_skill_gaps()
        high = [g["skill"] for g in gaps if g.get("priority") == "high"]
        others = [g["skill"] for g in gaps if g.get("priority") != "high"]
        top_gaps = "、".join((high + others)[:3]) or "无"

        prompt = _ADVICE_PROMPT_WITH_DIAGNOSIS.format(
            profile_summary=profile_summary,
            company=target.company,
            title=target.title,
            status_label=status_label,
            days_since_create=days_since_create,
            match_score=target.match_score if target.match_score is not None else 0,
            top_gaps=top_gaps,
        )
    else:
        prompt = _ADVICE_PROMPT_NO_DIAGNOSIS.format(
            profile_summary=profile_summary,
            company=target.company,
            title=target.title,
            status_label=status_label,
            days_since_create=days_since_create,
        )

    text = await llm_chat(
        task_type="general_chat",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=200,
    )
    return text.strip()


# ── Helpers ───────────────────────────────────────────


async def _load_target(db: AsyncSession, user_id: str, target_id: str) -> JobTarget:
    """按主键与用户双条件加载；防止通过猜测 UUID 读取他人岗位。"""
    target = (
        await db.execute(
            select(JobTarget).where(
                JobTarget.target_id == target_id,
                JobTarget.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="岗位不存在")
    return target


async def _next_sort_order(db: AsyncSession, user_id: str, status: str) -> int:
    """计算同一 user_id + status 下下一个 sort_order（追加到列尾）。"""
    result = await db.execute(
        select(func.max(JobTarget.sort_order)).where(
            JobTarget.user_id == user_id,
            JobTarget.status == status,
        )
    )
    current_max = result.scalar()
    return (current_max + 1) if current_max is not None else 0


async def _load_diagnosis_dict(db: AsyncSession, diagnosis_id: str | None) -> dict | None:
    """将 JDDiagnosis ORM 转为与前端 JD 诊断页一致的 dict（含 skill_gaps 结构化）。"""
    if not diagnosis_id:
        return None
    diagnosis = (
        await db.execute(select(JDDiagnosis).where(JDDiagnosis.diagnosis_id == diagnosis_id))
    ).scalar_one_or_none()
    if not diagnosis:
        return None
    return _diagnosis_to_dict(diagnosis)


def _diagnosis_to_dict(d: JDDiagnosis) -> dict:
    """把 JSON 列里的半结构化字段灌进 Pydantic，再 model_dump 成纯 dict。"""
    rdata = d.result_data or {}
    return JDDiagnoseResponse(
        diagnosis_id=d.diagnosis_id,
        jd_text=d.jd_text,
        jd_title=d.jd_title or "未命名 JD",
        overall_score=d.overall_score,
        summary=d.summary or "",
        skill_gaps=[GapSkill(**g) for g in rdata.get("skill_gaps", []) if isinstance(g, dict)],
        matched_skills=rdata.get("matched_skills", []),
        strengths=rdata.get("strengths", []),
        risks=rdata.get("risks", []),
        resume_tips=rdata.get("resume_tips", []),
        action_plan=rdata.get("action_plan", []),
    ).model_dump()


async def _aggregate_gaps(db: AsyncSession, diagnosis_ids: list[str]) -> list[str]:
    """跨多张诊断聚合技能缺口：按 skill 名称（忽略大小写）计数，保留首次出现的展示拼写。

    用于看板顶部「高频缺口」；空输入或无 gap 时返回空列表。
    """
    if not diagnosis_ids:
        return []

    result = await db.execute(select(JDDiagnosis).where(JDDiagnosis.diagnosis_id.in_(diagnosis_ids)))
    diagnoses = result.scalars().all()

    counter: Counter[str] = Counter()
    label_of: dict[str, str] = {}  # key: lower 归一化；value: 用户可见的首个 label
    for d in diagnoses:
        for gap in d.get_skill_gaps():
            skill = gap.get("skill") if isinstance(gap, dict) else None
            if not skill:
                continue
            key = skill.strip().lower()
            if not key:
                continue
            counter[key] += 1
            label_of.setdefault(key, skill.strip())

    return [label_of[key] for key, _ in counter.most_common(5)]


def _to_card(t: JobTarget) -> TargetCard:
    """ORM → 看板卡片 DTO；时间统一 ISO 字符串。"""
    return TargetCard(
        target_id=t.target_id,
        company=t.company,
        title=t.title,
        status=t.status,
        interview_round=t.interview_round,
        match_score=t.match_score,
        agent_advice=t.agent_advice,
        location=t.location,
        created_at=t.created_at.isoformat() if t.created_at else "",
    )


def _to_detail(t: JobTarget, diagnosis: dict | None) -> TargetDetail:
    """ORM + 可选诊断 dict → 详情 DTO。"""
    return TargetDetail(
        target_id=t.target_id,
        company=t.company,
        title=t.title,
        status=t.status,
        interview_round=t.interview_round,
        match_score=t.match_score,
        agent_advice=t.agent_advice,
        location=t.location,
        created_at=t.created_at.isoformat() if t.created_at else "",
        jd_text=t.jd_text,
        jd_url=t.jd_url,
        salary=t.salary,
        notes=t.notes,
        diagnosis=diagnosis,
    )
