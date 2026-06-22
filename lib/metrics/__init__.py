"""Metrics 采集层。

统一的时间序列表 metric_events + 进程单例 MetricsRecorder。
所有埋点都走同一个 record(name, value, labels) 接口，仪表盘按 name + 时间窗聚合。

为什么用统一表而非每类指标独立表：
- 避免 agent_traces 那种 schema 错位再次发生
- 新增指标无需改 schema（只加一个埋点调用）
- 仪表盘聚合逻辑统一
"""

from lib.metrics.models import MetricEvent
from lib.metrics.recorder import (
    MetricsRecorder,
    get_recorder,
    record,
    set_recorder,
)

__all__ = [
    "MetricEvent",
    "MetricsRecorder",
    "get_recorder",
    "record",
    "set_recorder",
]
