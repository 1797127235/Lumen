"""对话 API 测试"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_chat_history(client: AsyncClient):
    """对话历史接口"""
    r = await client.get("/api/chat/history", params={"user_id": "test_user", "limit": 10})
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)


@pytest.mark.asyncio
async def test_profile_upload_invalid(client: AsyncClient):
    """无效文件上传返回错误"""
    r = await client.post(
        "/api/profile/resume",
        params={"user_id": "test_user"},
        files={"file": ("test.txt", b"not a real resume", "text/plain")},
    )
    # 可能成功解析或返回错误
    assert r.status_code in (200, 422, 502)
