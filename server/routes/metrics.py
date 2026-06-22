"""Metrics 仪表盘 API — 聚合查询 metric_events 表。

所有端点用 get_db() 依赖注入走主库 ORM（数据在 lumen.db，不是 sidecar）。
时间窗用 ?range=1h|24h|7d|30d 控制，时序桶用 ?bucket=hour|day 控制。
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from lib.metrics.models import MetricEvent
from shared.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["metrics"])

# range 字符串 → timedelta
_RANGE_DELTAS: dict[str, timedelta] = {
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}

# bucket 字符串 → SQLite strftime 格式 + 对应的 seconds 粒度（用于前端显示）
_BUCKET_FMT: dict[str, str] = {
    "hour": "%Y-%m-%dT%H:00:00",
    "day": "%Y-%m-%d",
}


def _parse_range(range_str: str) -> datetime:
    """range 字符串 → since datetime（UTC）。未知值兜底为 24h。"""
    delta = _RANGE_DELTAS.get(range_str, _RANGE_DELTAS["24h"])
    return datetime.now(UTC) - delta


def _parse_bucket(bucket_str: str) -> str:
    """bucket 字符串 → SQLite strftime 格式。未知值兜底为 hour。"""
    return _BUCKET_FMT.get(bucket_str, _BUCKET_FMT["hour"])


# ── 端点 1：KPI 摘要 ─────────────────────────────────────────────


@router.get("/metrics/summary")
async def get_metrics_summary(
    range: str = Query("24h", description="1h|24h|7d|30d"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """KPI 卡片数据：总 token / 估算成本 / 对话数 / 平均延迟 / 错误率。

    cost 估算用 models.dev 定价的粗近似（输入 + 输出 token 按通用价 0.5/1.5 USD/1M），
    真实定价需要 provider + model 解析，留作后续增强。
    """
    since = _parse_range(range)

    # 按 name 聚合：求和 token / 计数 turn / 计数 error
    stmt = (
        select(MetricEvent.name, func.sum(MetricEvent.value), func.count())
        .where(MetricEvent.created_at >= since)
        .group_by(MetricEvent.name)
    )
    rows = (await db.execute(stmt)).all()

    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    for name, total_val, cnt in rows:
        totals[name] = float(total_val or 0)
        counts[name] = int(cnt or 0)

    # 估算成本（粗近似，单位 USD）
    input_tokens = totals.get("llm.tokens.input", 0)
    output_tokens = totals.get("llm.tokens.output", 0)
    # 通用价：input $0.5/1M, output $1.5/1M（仅占位，真实定价见 models_dev.py）
    estimated_cost = (input_tokens * 0.5 + output_tokens * 1.5) / 1_000_000

    turn_total = counts.get("turn.completed", 0)
    error_turns = 0
    # 错误 turn 需要按 outcome=error 计数，从原始事件查
    err_stmt = (
        select(func.count())
        .select_from(MetricEvent)
        .where(
            MetricEvent.name == "turn.completed",
            MetricEvent.created_at >= since,
            MetricEvent.labels_json.like('%"outcome": "error"%'),
        )
    )
    error_turns = int((await db.execute(err_stmt)).scalar() or 0)
    error_rate = (error_turns / turn_total) if turn_total else 0.0

    # 平均 turn 延迟
    turn_duration = totals.get("turn.duration_ms", 0)
    turn_count = counts.get("turn.duration_ms", 0)
    avg_turn_latency = (turn_duration / turn_count) if turn_count else 0.0

    return {
        "range": range,
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens),
        "cache_read_tokens": int(totals.get("llm.tokens.cache_read", 0)),
        "cache_write_tokens": int(totals.get("llm.tokens.cache_write", 0)),
        "estimated_cost_usd": round(estimated_cost, 4),
        "llm_calls": counts.get("llm.calls", 0),
        "context_overflows": counts.get("llm.context_overflow", 0),
        "turns": turn_total,
        "error_turns": error_turns,
        "error_rate": round(error_rate, 4),
        "avg_turn_latency_ms": round(avg_turn_latency, 1),
        "tool_calls": counts.get("tool.calls", 0),
        "deliveries": counts.get("proactive.deliveries", 0),
        "memory_writes": counts.get("memory.writes", 0),
    }


# ── 端点 2：用量时序 ─────────────────────────────────────────────


@router.get("/metrics/usage")
async def get_usage_series(
    range: str = Query("7d"),
    bucket: str = Query("day", description="hour|day"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """token 用量时序：输入/输出/cache_read/cache_write 各一条线。"""
    since = _parse_range(range)
    fmt = _parse_bucket(bucket)

    names = (
        "llm.tokens.input",
        "llm.tokens.output",
        "llm.tokens.cache_read",
        "llm.tokens.cache_write",
    )
    stmt = (
        select(
            func.strftime(fmt, MetricEvent.created_at).label("bucket"),
            MetricEvent.name,
            func.sum(MetricEvent.value).label("total"),
        )
        .where(MetricEvent.name.in_(names), MetricEvent.created_at >= since)
        .group_by("bucket", MetricEvent.name)
        .order_by("bucket")
    )
    rows = (await db.execute(stmt)).all()

    # 转成 { ts: { input: N, output: M, ... } } 便于前端绘制多线图
    series: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for bkt, name, total in rows:
        short_name = name.split(".")[-1]  # input / output / cache_read / cache_write
        series[bkt][short_name] = float(total or 0)

    points = [{"ts": ts, **vals} for ts, vals in sorted(series.items())]
    return {"range": range, "bucket": bucket, "points": points}


# ── 端点 3：延迟时序 ─────────────────────────────────────────────


@router.get("/metrics/latency")
async def get_latency_series(
    range: str = Query("7d"),
    bucket: str = Query("day"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """延迟时序：turn / llm / http / tool 各一条线（avg per bucket）。"""
    since = _parse_range(range)
    fmt = _parse_bucket(bucket)

    names = (
        "turn.duration_ms",
        "llm.call_duration_ms",
        "http.request_duration_ms",
        "tool.duration_ms",
    )
    stmt = (
        select(
            func.strftime(fmt, MetricEvent.created_at).label("bucket"),
            MetricEvent.name,
            func.avg(MetricEvent.value).label("avg"),
        )
        .where(MetricEvent.name.in_(names), MetricEvent.created_at >= since)
        .group_by("bucket", MetricEvent.name)
        .order_by("bucket")
    )
    rows = (await db.execute(stmt)).all()

    series: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for bkt, name, avg in rows:
        # turn.duration_ms → turn, llm.call_duration_ms → llm, ...
        short = name.split(".")[0]
        series[bkt][short] = round(float(avg or 0), 1)

    points = [{"ts": ts, **vals} for ts, vals in sorted(series.items())]
    return {"range": range, "bucket": bucket, "points": points}


# ── 端点 4：工具调用排行 ─────────────────────────────────────────


@router.get("/metrics/tools")
async def get_tool_stats(
    range: str = Query("24h"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """工具调用排行：按 tool_name 聚合调用数、平均耗时、错误数。

    labels_json 是字符串，无法直接 GROUP BY，需在 Python 侧聚合。
    """
    since = _parse_range(range)

    stmt = select(MetricEvent.labels_json, MetricEvent.name, MetricEvent.value).where(
        MetricEvent.name.in_(("tool.calls", "tool.duration_ms")),
        MetricEvent.created_at >= since,
    )
    rows = (await db.execute(stmt)).all()

    # {tool_name: {calls: N, total_duration_ms: M, errors: K}}
    tools: dict[str, dict[str, float]] = defaultdict(lambda: {"calls": 0, "total_duration_ms": 0, "errors": 0})
    for labels_json, name, value in rows:
        try:
            labels = json.loads(labels_json or "{}")
        except json.JSONDecodeError:
            labels = {}
        tool_name = labels.get("tool_name", "unknown")
        if name == "tool.calls":
            tools[tool_name]["calls"] += 1
            if labels.get("status") == "error":
                tools[tool_name]["errors"] += 1
        elif name == "tool.duration_ms":
            tools[tool_name]["total_duration_ms"] += float(value or 0)

    items = []
    for tool_name, stats in tools.items():
        calls = stats["calls"]
        items.append(
            {
                "tool_name": tool_name,
                "calls": calls,
                "errors": int(stats["errors"]),
                "avg_duration_ms": round(stats["total_duration_ms"] / calls, 1) if calls else 0,
            }
        )
    items.sort(key=lambda x: x["calls"], reverse=True)
    return {"range": range, "items": items}


# ── 端点 5：原始事件流 ───────────────────────────────────────────


@router.get("/metrics/events")
async def list_metric_events(
    name: str = Query("", description="按指标名过滤"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """原始事件流分页（最近优先）。page_size 上限 200。"""
    base = select(MetricEvent)
    count_base = select(func.count()).select_from(MetricEvent)
    if name:
        base = base.where(MetricEvent.name == name)
        count_base = count_base.where(MetricEvent.name == name)

    total = int((await db.execute(count_base)).scalar() or 0)

    stmt = base.order_by(MetricEvent.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(stmt)).scalars().all()

    items = [
        {
            "id": r.id,
            "name": r.name,
            "value": r.value,
            "labels": json.loads(r.labels_json or "{}"),
            "conversation_id": r.conversation_id,
            "user_id": r.user_id,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }
