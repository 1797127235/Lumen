"""Memory Provider 配置模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class MemoryProviderConfig(BaseModel):
    """单个记忆 Provider 的运行时配置。

    持久化在 ~/.lumen/config.json["memory_providers"] 中。
    """

    name: str = Field(..., description="实例唯一名，用于日志和路由")
    provider_type: str = Field(..., description="插件类型，对应 builtins/<type>/ 或 ~/.lumen/plugins/memory/<type>/")
    enabled: bool = Field(True, description="是否启用")
    config: dict = Field(default_factory=dict, description="传给 Provider 构造函数的参数字典")
