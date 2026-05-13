"""DataSource API 请求/响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class DataSourceBase(BaseModel):
    """公共字段。"""

    name: str = Field(..., min_length=1, max_length=100)
    type: str = Field(..., pattern=r"^(local_folder|web_url|github_repo)$")
    config: dict[str, Any] = Field(default_factory=dict)


class DataSourceCreate(DataSourceBase):
    """创建数据源请求。"""

    pass


class DataSourceUpdate(BaseModel):
    """更新数据源请求（全字段可选）。"""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    status: str | None = Field(default=None, pattern=r"^(active|paused|error)$")
    config: dict[str, Any] | None = None


class DataSourceRead(DataSourceBase):
    """数据源响应。"""

    id: str
    user_id: str
    status: str
    capabilities: list[str] = Field(default_factory=list)
    last_sync_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
