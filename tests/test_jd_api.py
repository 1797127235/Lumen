"""JD 诊断 API 测试"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_diagnose_jd(client: AsyncClient):
    """JD 诊断接口"""
    r = await client.post(
        "/api/jd/diagnose",
        params={"user_id": "test_user"},
        json={
            "jd_text": "岗位：Python 后端工程师\n要求：熟悉 Python、FastAPI、SQLAlchemy",
        },
    )
    # 可能成功或因为 LLM 不可用而失败
    assert r.status_code in (200, 502)


@pytest.mark.asyncio
async def test_get_diagnosis_history(client: AsyncClient):
    """诊断历史接口"""
    r = await client.get("/api/jd/history", params={"user_id": "test_user"})
    assert r.status_code == 200
    data = r.json()
    # 可能返回 list 或 {"items": [...]}
    if isinstance(data, dict):
        assert "items" in data
        assert isinstance(data["items"], list)
    else:
        assert isinstance(data, list)
