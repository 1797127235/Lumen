"""事件触发 — 订阅元数据持久化(JSON)。

镜像 lib/scheduler/store.py:不依赖 MCP、不依赖消息体系,只管 SubscriptionSpec 的读写。
路径由上层(listener)注入,本模块不假设数据落在哪。

与 scheduler 的 TaskSpec 对称:TaskSpec 描述「到点调工具」,
SubscriptionSpec 描述「收到某 server 的事件通知 → 注入对话」。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from shared.logging import get_logger

logger = get_logger(__name__)


@dataclass
class SubscriptionSpec:
    """一个事件订阅的元数据(可 JSON 序列化)。

    server_name + event_filter 决定匹配哪些 MCP notification;
    user_id 记录订阅者(送达时作 sender,让 agent 用正确记忆)。
    订阅不绑渠道——命中后交给送达层(delivery.py)决定推给谁(单用户第一版 Telegram)。
    """

    id: str
    server_name: str  # MCP server 名(匹配哪个 server 发的通知)
    event_filter: str = ""  # notification method 过滤(如 "resources/updated"),空=该 server 所有通知
    user_id: str = ""  # 订阅时的用户 id(送达时作 sender)
    name: str | None = None
    created_at: str = ""
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SubscriptionSpec:
        return cls(
            id=d["id"],
            server_name=d["server_name"],
            event_filter=d.get("event_filter", ""),
            user_id=d.get("user_id", ""),
            name=d.get("name"),
            created_at=d.get("created_at", ""),
            enabled=d.get("enabled", True),
        )


class SubscriptionStore:
    """JSON 文件持久化 SubscriptionSpec 列表。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> list[SubscriptionSpec]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return [SubscriptionSpec.from_dict(d) for d in data]
        except Exception as exc:
            logger.warning("订阅持久化读取失败: %s", exc)
            return []

    def save(self, subs: list[SubscriptionSpec]) -> None:
        self.path.write_text(
            json.dumps([s.to_dict() for s in subs], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def upsert(self, sub: SubscriptionSpec) -> None:
        subs = self.load()
        for i, s in enumerate(subs):
            if s.id == sub.id:
                subs[i] = sub
                self.save(subs)
                return
        subs.append(sub)
        self.save(subs)

    def remove(self, sub_id: str) -> bool:
        subs = self.load()
        new = [s for s in subs if s.id != sub_id]
        if len(new) == len(subs):
            return False
        self.save(new)
        return True

    def list_all(self) -> list[SubscriptionSpec]:
        return self.load()
