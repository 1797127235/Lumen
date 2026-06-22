"""Memory Provider 管理 API。

提供已发现插件列表、当前配置、加载状态的管理，以及连通性测试。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from lib.memory import (
    discover_builtin_providers,
    discover_providers,
    discover_user_providers,
    get_memory_manager,
    load_provider,
)
from lib.memory.config_store import (
    add_memory_provider_config,
    load_memory_provider_configs,
    remove_memory_provider_config,
    update_memory_provider_config,
)
from lib.memory.models import MemoryProviderConfig
from shared.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["memory-providers"])


class MemoryProviderCreate(BaseModel):
    name: str
    provider_type: str
    enabled: bool = True
    config: dict[str, Any] = {}


class MemoryProviderUpdate(BaseModel):
    provider_type: str | None = None
    enabled: bool | None = None
    config: dict[str, Any] | None = None


class MemoryProviderResponse(BaseModel):
    name: str
    provider_type: str
    enabled: bool
    config: dict[str, Any]


class InstalledProviderResponse(BaseModel):
    name: str
    provider_type: str
    display_name: str


class TestResponse(BaseModel):
    ok: bool
    error: str = ""


@router.get("/memory/providers")
async def list_memory_providers() -> dict[str, list[dict[str, Any]]]:
    """返回已发现插件 + 当前启用配置。"""
    discovered = discover_providers()
    configured = load_memory_provider_configs()
    builtin_names = set(discover_builtin_providers().keys())
    user_names = set(discover_user_providers().keys())

    result_discovered: list[dict[str, Any]] = []
    for name in sorted(discovered.keys()):
        result_discovered.append(
            {
                "name": name,
                "builtin": name in builtin_names,
                "user_override": name in user_names,
            }
        )

    return {
        "discovered": result_discovered,
        "configured": [cfg.model_dump() for cfg in configured],
    }


@router.get("/memory/providers/installed")
async def list_installed_providers() -> list[InstalledProviderResponse]:
    """返回当前已加载的 provider 名称列表。"""
    manager = get_memory_manager()
    return [
        InstalledProviderResponse(
            name=p.instance_name or p.name,
            provider_type=p.name,
            display_name=p.display_name,
        )
        for p in manager.providers
        if p.name != "builtin"
    ]


@router.post("/memory/providers", response_model=MemoryProviderResponse)
async def create_memory_provider(body: MemoryProviderCreate) -> MemoryProviderResponse:
    """添加并保存一个 provider 配置。"""
    discovered = discover_providers()
    if body.provider_type not in discovered:
        raise HTTPException(
            status_code=400,
            detail=f"未找到 provider 插件: {body.provider_type}",
        )

    config = MemoryProviderConfig(
        name=body.name,
        provider_type=body.provider_type,
        enabled=body.enabled,
        config=body.config,
    )
    add_memory_provider_config(config)
    return MemoryProviderResponse(**config.model_dump())


@router.put("/memory/providers/{name}", response_model=MemoryProviderResponse)
async def update_memory_provider(name: str, body: MemoryProviderUpdate) -> MemoryProviderResponse:
    """更新 provider 配置。"""
    patch: dict[str, Any] = {}
    if body.provider_type is not None:
        patch["provider_type"] = body.provider_type
    if body.enabled is not None:
        patch["enabled"] = body.enabled
    if body.config is not None:
        patch["config"] = body.config

    updated = update_memory_provider_config(name, patch)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"未找到 provider 配置: {name}")
    return MemoryProviderResponse(**updated.model_dump())


@router.delete("/memory/providers/{name}")
async def delete_memory_provider(name: str) -> dict[str, bool]:
    """删除配置并卸载已加载的实例。"""
    removed = remove_memory_provider_config(name)
    if not removed:
        raise HTTPException(status_code=404, detail=f"未找到 provider 配置: {name}")

    manager = get_memory_manager()
    manager.remove_provider(name)
    return {"removed": True}


@router.post("/memory/providers/{name}/test", response_model=TestResponse)
async def test_memory_provider(name: str) -> TestResponse:
    """测试指定 provider 的连通性。"""
    configs = load_memory_provider_configs()
    cfg = next((c for c in configs if c.name == name), None)
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"未找到 provider 配置: {name}")

    provider = load_provider(cfg.provider_type, config=cfg.config)
    if provider is None:
        return TestResponse(ok=False, error="实例化失败")

    try:
        ok = await provider.is_available()
        return TestResponse(ok=ok, error="" if ok else "连通性检查失败")
    except Exception as exc:
        logger.warning("memory provider 测试失败", name=name, error=str(exc))
        return TestResponse(ok=False, error=f"{type(exc).__name__}: {exc}")


@router.post("/memory/providers/{name}/reload")
async def reload_memory_provider(name: str) -> dict[str, Any]:
    """重新加载 provider 实例。"""
    configs = load_memory_provider_configs()
    cfg = next((c for c in configs if c.name == name), None)
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"未找到 provider 配置: {name}")

    manager = get_memory_manager()
    manager.remove_provider(name)

    if not cfg.enabled:
        return {"reloaded": False, "reason": "disabled"}

    provider = load_provider(cfg.provider_type, config=cfg.config)
    if provider is None:
        raise HTTPException(status_code=500, detail="实例化失败")

    if await provider.is_available():
        manager.add_provider(provider, instance_name=cfg.name)
        return {"reloaded": True, "name": name}
    return {"reloaded": False, "reason": "unavailable"}


@router.get("/memory/providers/pending")
async def list_pending_providers() -> list[dict[str, Any]]:
    """查询待激活队列状态。"""
    manager = get_memory_manager()
    return manager.get_pending_providers()


@router.post("/memory/providers/reconcile")
async def reconcile_providers() -> dict[str, Any]:
    """手动触发一次 reconcile，尝试激活待激活的 providers。"""
    manager = get_memory_manager()
    return await manager.reconcile_now()
