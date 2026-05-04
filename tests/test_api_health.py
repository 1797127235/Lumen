"""API 基础测试 — 健康检查 + 核心端点可用性"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health(client: AsyncClient):
    """健康检查端点返回 200"""
    r = await client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_get_profile(client: AsyncClient):
    """用户画像端点可访问"""
    r = await client.get("/api/profile/me", params={"user_id": "nonexistent"})
    # 可能返回 200 (null) 或 404，取决于实现
    assert r.status_code in (200, 404)


@pytest.mark.asyncio
async def test_chat_history_empty(client: AsyncClient):
    """空用户的对话历史返回空列表"""
    r = await client.get("/api/chat/history", params={"user_id": "nonexistent", "limit": 10})
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) == 0
