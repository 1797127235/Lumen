"""Channel 配置模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ChannelConfig(BaseModel):
    """单个 channel 实例配置。"""

    name: str
    provider_type: str
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)
