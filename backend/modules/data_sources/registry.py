"""数据源连接器注册表 — 按 type 创建对应 Connector 实例。"""

from __future__ import annotations

from typing import TypeVar

from backend.core.logging import get_logger
from backend.modules.data_sources.ingestion.connector import DataSourceConnector
from backend.modules.data_sources.ingestion.connectors.local_folder import FilesystemConnector
from backend.modules.data_sources.models import DataSource

logger = get_logger(__name__)

T = TypeVar("T", bound=DataSourceConnector)


_CONNECTOR_MAP: dict[str, type[DataSourceConnector]] = {
    "local_folder": FilesystemConnector,
}


def list_connector_types() -> list[str]:
    """返回已注册的连接器类型列表。"""
    return list(_CONNECTOR_MAP.keys())


def get_connector_class(type_name: str) -> type[DataSourceConnector] | None:
    """按类型名获取连接器类。"""
    return _CONNECTOR_MAP.get(type_name)


def create_connector(source: DataSource) -> DataSourceConnector | None:
    """根据 DataSource 配置创建对应的 Connector 实例。"""
    cls = get_connector_class(source.type)
    if cls is None:
        logger.warning("unknown_connector_type", type=source.type, source_id=source.id)
        return None

    config = source.config_json or {}

    if source.type == "local_folder":
        paths = config.get("path") or config.get("paths") or []
        if isinstance(paths, str):
            paths = [paths]
        if not paths:
            logger.warning("local_folder_no_path", source_id=source.id)
            return None
        return FilesystemConnector(
            directories=paths,
            user_id=source.user_id,
            data_source_id=source.id,
        )

    # 后续扩展 web_url / github_repo
    return None
