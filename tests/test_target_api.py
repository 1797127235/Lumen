"""岗位追踪 API 测试 — 三条建卡路径 + CRUD"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_target_path_c(client: AsyncClient):
    """Path C: 手动建卡（无诊断、无 JD）"""
    r = await client.post(
        "/api/targets",
        params={"user_id": "test_user"},
        json={
            "title": "Python 后端工程师",
            "company": "测试公司",
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["title"] == "Python 后端工程师"
    assert data["company"] == "测试公司"
    assert data["status"] == "interested"
    assert "target_id" in data


@pytest.mark.asyncio
async def test_get_board(client: AsyncClient):
    """看板接口返回正确结构"""
    # 先建一张卡
    await client.post(
        "/api/targets",
        params={"user_id": "test_user"},
        json={"title": "测试岗位", "company": "测试公司"},
    )

    r = await client.get("/api/targets/board", params={"user_id": "test_user"})
    assert r.status_code == 200
    data = r.json()
    assert "columns" in data
    assert "stats" in data
    assert isinstance(data["columns"], dict)


@pytest.mark.asyncio
async def test_get_target_detail(client: AsyncClient):
    """单卡详情接口"""
    # 先建卡
    create_r = await client.post(
        "/api/targets",
        params={"user_id": "test_user"},
        json={"title": "详情测试岗位", "company": "详情测试公司"},
    )
    target_id = create_r.json()["target_id"]

    # 获取详情
    r = await client.get(f"/api/targets/{target_id}", params={"user_id": "test_user"})
    assert r.status_code == 200
    data = r.json()
    assert data["target_id"] == target_id
    assert data["title"] == "详情测试岗位"


@pytest.mark.asyncio
async def test_update_target(client: AsyncClient):
    """更新岗位状态"""
    # 先建卡
    create_r = await client.post(
        "/api/targets",
        params={"user_id": "test_user"},
        json={"title": "更新测试岗位", "company": "更新测试公司"},
    )
    target_id = create_r.json()["target_id"]

    # 更新状态
    r = await client.patch(
        f"/api/targets/{target_id}",
        params={"user_id": "test_user"},
        json={"status": "applied"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "applied"


@pytest.mark.asyncio
async def test_delete_target(client: AsyncClient):
    """删除岗位卡片"""
    # 先建卡
    create_r = await client.post(
        "/api/targets",
        params={"user_id": "test_user"},
        json={"title": "删除测试岗位", "company": "删除测试公司"},
    )
    target_id = create_r.json()["target_id"]

    # 删除
    r = await client.delete(f"/api/targets/{target_id}", params={"user_id": "test_user"})
    assert r.status_code == 200
    assert r.json()["deleted"] is True

    # 确认已删除
    r = await client.get(f"/api/targets/{target_id}", params={"user_id": "test_user"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_target_not_found(client: AsyncClient):
    """不存在的岗位返回 404"""
    r = await client.get("/api/targets/nonexistent", params={"user_id": "test_user"})
    assert r.status_code == 404
