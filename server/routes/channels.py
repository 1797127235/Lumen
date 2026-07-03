"""Channel 管理 API。

提供已发现 channel provider 列表、当前配置、加载状态的管理，以及连通性测试。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from channels.config_store import (
    add_channel_config,
    load_channel_configs,
    remove_channel_config,
    update_channel_config,
)
from channels.manager import ChannelManager
from channels.models import ChannelConfig
from lib.bus.event_bus import EventBus
from lib.bus.queue import MessageBus
from shared.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["channels"])


class ChannelCreate(BaseModel):
    name: str
    provider_type: str
    enabled: bool = True
    config: dict[str, Any] = {}


class ChannelUpdate(BaseModel):
    provider_type: str | None = None
    enabled: bool | None = None
    config: dict[str, Any] | None = None


class ChannelResponse(BaseModel):
    name: str
    provider_type: str
    enabled: bool
    config: dict[str, Any]


class DiscoveredChannelResponse(BaseModel):
    name: str
    builtin: bool


class TestResponse(BaseModel):
    ok: bool
    error: str = ""


@router.get("/channels")
async def list_channels() -> dict[str, list[dict[str, Any]]]:
    """返回已发现 channel provider + 当前配置。"""
    # 临时 ChannelManager 仅用于发现 provider，不需要真实 bus/event_bus
    manager = ChannelManager(bus=MessageBus(), event_bus=EventBus())
    discovered = manager.discover_providers()

    # builtin 目录中的为内置
    from channels.manager import BUILTIN_PLUGINS_DIR
    from channels.provider import ChannelProvider
    from lib.plugins.loader import discover_plugins

    builtin_names = set(
        discover_plugins(
            builtin_dir=BUILTIN_PLUGINS_DIR,
            user_dir=None,
            base_class=ChannelProvider,
        ).keys()
    )

    configured = load_channel_configs()

    result_discovered: list[dict[str, Any]] = []
    for name in sorted(discovered.keys()):
        result_discovered.append(
            {
                "name": name,
                "builtin": name in builtin_names,
            }
        )

    return {
        "discovered": result_discovered,
        "configured": [cfg.model_dump() for cfg in configured],
    }


@router.post("/channels", response_model=ChannelResponse)
async def create_channel(body: ChannelCreate) -> ChannelResponse:
    """添加并保存一个 channel 配置。"""
    manager = ChannelManager(bus=MessageBus(), event_bus=EventBus())
    discovered = manager.discover_providers()
    if body.provider_type not in discovered:
        raise HTTPException(
            status_code=400,
            detail=f"未找到 channel provider: {body.provider_type}",
        )

    config = ChannelConfig(
        name=body.name,
        provider_type=body.provider_type,
        enabled=body.enabled,
        config=body.config,
    )
    add_channel_config(config)
    return ChannelResponse(**config.model_dump())


@router.put("/channels/{name}", response_model=ChannelResponse)
async def update_channel(name: str, body: ChannelUpdate) -> ChannelResponse:
    """更新 channel 配置。"""
    patch: dict[str, Any] = {}
    if body.provider_type is not None:
        patch["provider_type"] = body.provider_type
    if body.enabled is not None:
        patch["enabled"] = body.enabled
    if body.config is not None:
        patch["config"] = body.config

    updated = update_channel_config(name, patch)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"未找到 channel 配置: {name}")
    return ChannelResponse(**updated.model_dump())


@router.delete("/channels/{name}")
async def delete_channel(name: str) -> dict[str, bool]:
    """删除 channel 配置。"""
    removed = remove_channel_config(name)
    if not removed:
        raise HTTPException(status_code=404, detail=f"未找到 channel 配置: {name}")
    return {"removed": True}


@router.post("/channels/{name}/test", response_model=TestResponse)
async def test_channel(name: str) -> TestResponse:
    """测试指定 channel 配置是否可以实例化。"""
    configs = load_channel_configs()
    cfg = next((c for c in configs if c.name == name), None)
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"未找到 channel 配置: {name}")

    manager = ChannelManager(bus=MessageBus(), event_bus=EventBus())
    provider = manager.load_provider_instance(cfg.provider_type, config=cfg.config)
    if provider is None:
        return TestResponse(ok=False, error="实例化失败")

    # 部分 provider（如 Telegram）需要实际网络请求才能验证 token
    # 这里只做最小化验证：build 成功即视为可实例化
    try:
        channel = provider.build(
            cfg.config,
            bus=MessageBus(),
            event_bus=EventBus(),
        )
        _ = channel.name
        return TestResponse(ok=True)
    except Exception as exc:
        logger.warning("channel 测试失败", name=name, error=str(exc))
        return TestResponse(ok=False, error=f"{type(exc).__name__}: {exc}")
