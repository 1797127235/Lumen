"""数据源连接管理测试 — CRUD + 测试连接 + 暂停/恢复。"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.data_sources.service import (
    create_data_source,
    delete_data_source,
    get_data_source,
    list_data_sources,
    update_data_source,
)

# ── Service 层 ──


async def test_create_and_list(db: AsyncSession) -> None:
    """创建数据源后应能列出。"""
    ds = await create_data_source(
        db, user_id="demo_user", name="我的 Obsidian", type="local_folder", config={"path": "/tmp/notes"}
    )
    assert ds.id.startswith("ds_")
    assert ds.status == "active"
    assert ds.capabilities_json == ["scan", "watch"]

    sources = await list_data_sources(db, "demo_user")
    assert len(sources) == 1
    assert sources[0].name == "我的 Obsidian"


async def test_get_and_update(db: AsyncSession) -> None:
    """获取和更新数据源。"""
    ds = await create_data_source(
        db, user_id="demo_user", name="Old Name", type="local_folder", config={"path": "/tmp"}
    )

    found = await get_data_source(db, "demo_user", ds.id)
    assert found is not None
    assert found.name == "Old Name"

    updated = await update_data_source(db, "demo_user", ds.id, name="New Name", status="paused")
    assert updated is not None
    assert updated.name == "New Name"
    assert updated.status == "paused"

    # 其他用户应无法访问
    other = await get_data_source(db, "other_user", ds.id)
    assert other is None


async def test_delete_source(db: AsyncSession) -> None:
    """删除数据源应成功。"""
    ds = await create_data_source(
        db, user_id="demo_user", name="To Delete", type="local_folder", config={"path": "/tmp"}
    )

    ok = await delete_data_source(db, "demo_user", ds.id)
    assert ok is True

    gone = await get_data_source(db, "demo_user", ds.id)
    assert gone is None

    # 重复删除应返回 False
    ok2 = await delete_data_source(db, "demo_user", ds.id)
    assert ok2 is False


async def test_filter_by_status(db: AsyncSession) -> None:
    """按状态过滤数据源。"""
    await create_data_source(db, user_id="u1", name="Active", type="local_folder", config={})
    ds2 = await create_data_source(db, user_id="u1", name="Paused", type="local_folder", config={})
    await update_data_source(db, "u1", ds2.id, status="paused")

    all_sources = await list_data_sources(db, "u1")
    assert len(all_sources) == 2

    active_only = await list_data_sources(db, "u1", status="active")
    assert len(active_only) == 1
    assert active_only[0].name == "Active"


# ── API 层 ──


@pytest.mark.asyncio
async def test_api_create_and_list(client: AsyncClient) -> None:
    """API: 创建后列出。"""
    r = await client.post(
        "/api/data-sources",
        json={"name": "API Test", "type": "local_folder", "config": {"path": "C:\\Notes"}},
    )
    assert r.status_code == 201
    data = r.json()
    assert data["name"] == "API Test"
    assert data["type"] == "local_folder"
    assert data["status"] == "active"
    source_id = data["id"]

    r2 = await client.get("/api/data-sources")
    assert r2.status_code == 200
    items = r2.json()
    assert any(i["id"] == source_id for i in items)


@pytest.mark.asyncio
async def test_api_get_patch_delete(client: AsyncClient) -> None:
    """API: 获取、更新、删除完整链路。"""
    # 创建
    r = await client.post(
        "/api/data-sources",
        json={"name": "Lifecycle", "type": "local_folder", "config": {}},
    )
    sid = r.json()["id"]

    # 获取
    r = await client.get(f"/api/data-sources/{sid}")
    assert r.status_code == 200
    assert r.json()["name"] == "Lifecycle"

    # 更新
    r = await client.patch(f"/api/data-sources/{sid}", json={"name": "Updated", "status": "paused"})
    assert r.status_code == 200
    assert r.json()["name"] == "Updated"
    assert r.json()["status"] == "paused"

    # 删除
    r = await client.delete(f"/api/data-sources/{sid}")
    assert r.status_code == 204

    # 再次获取应 404
    r = await client.get(f"/api/data-sources/{sid}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_api_test_connection(client: AsyncClient) -> None:
    """API: 测试连接。"""
    # 不存在的路径
    r = await client.post(
        "/api/data-sources",
        json={"name": "Bad Path", "type": "local_folder", "config": {"path": "Z:\\NonExistent"}},
    )
    sid = r.json()["id"]

    r = await client.post(f"/api/data-sources/{sid}/test")
    assert r.status_code == 200
    result = r.json()
    assert result["ok"] is False
    assert "不存在" in result["error"] or "无效" in result["error"]


@pytest.mark.asyncio
async def test_api_pause_resume(client: AsyncClient) -> None:
    """API: 暂停和恢复。"""
    r = await client.post(
        "/api/data-sources",
        json={"name": "Toggle", "type": "local_folder", "config": {}},
    )
    sid = r.json()["id"]

    r = await client.post(f"/api/data-sources/{sid}/pause")
    assert r.status_code == 200
    assert r.json()["status"] == "paused"

    r = await client.post(f"/api/data-sources/{sid}/resume")
    assert r.status_code == 200
    assert r.json()["status"] == "active"


@pytest.mark.asyncio
async def test_api_sync_not_found(client: AsyncClient) -> None:
    """API: 对不存在的数据源同步应 404。"""
    r = await client.post("/api/data-sources/ds_notexist/sync")
    assert r.status_code in (404, 503)
