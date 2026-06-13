from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AkashaConfig:
    """Akasha 运行时配置。"""

    db_path: str = ""
    dense_top_k: int = 10
    ripple_top_k: int = 10
    activate_limit: int = 8
    inject_max_chars: int = 6000
    assistant_preview_chars: int = 15
    dense_seed_threshold: float = 0.675
    nearby_time_seconds: int = 1800
    nearby_dense_threshold: float = 0.28
    activation_threshold: float = 0.22
    soft_recall_threshold: float = 0.165
    soft_recall_direct_floor: float = 0.45
    cross_boost: float = 36.0


DEFAULT_CONFIG = AkashaConfig()


def load_akasha_config(config: dict | None = None) -> AkashaConfig:
    """从 provider config dict 加载 AkashaConfig。"""
    cfg = config or {}
    return AkashaConfig(
        db_path=_str_value(cfg.get("db_path"), DEFAULT_CONFIG.db_path),
        dense_top_k=_int_value(cfg.get("dense_top_k"), DEFAULT_CONFIG.dense_top_k),
        ripple_top_k=_int_value(cfg.get("ripple_top_k"), DEFAULT_CONFIG.ripple_top_k),
        activate_limit=_int_value(cfg.get("activate_limit"), DEFAULT_CONFIG.activate_limit),
        inject_max_chars=_int_value(cfg.get("inject_max_chars"), DEFAULT_CONFIG.inject_max_chars),
        assistant_preview_chars=_int_value(cfg.get("assistant_preview_chars"), DEFAULT_CONFIG.assistant_preview_chars),
        dense_seed_threshold=_float_value(cfg.get("dense_seed_threshold"), DEFAULT_CONFIG.dense_seed_threshold),
        nearby_time_seconds=_int_value(cfg.get("nearby_time_seconds"), DEFAULT_CONFIG.nearby_time_seconds),
        nearby_dense_threshold=_float_value(cfg.get("nearby_dense_threshold"), DEFAULT_CONFIG.nearby_dense_threshold),
        activation_threshold=_float_value(cfg.get("activation_threshold"), DEFAULT_CONFIG.activation_threshold),
        soft_recall_threshold=_float_value(cfg.get("soft_recall_threshold"), DEFAULT_CONFIG.soft_recall_threshold),
        soft_recall_direct_floor=_float_value(
            cfg.get("soft_recall_direct_floor"), DEFAULT_CONFIG.soft_recall_direct_floor
        ),
        cross_boost=_float_value(cfg.get("cross_boost"), DEFAULT_CONFIG.cross_boost),
    )


def resolve_akasha_db_path(*, user_id: str, akasha_config: AkashaConfig) -> Path:
    """解析 Akasha sidecar 数据库路径。

    默认：~/.lumen/memory/{user_id}/akasha.db
    """
    from core.config import USER_DATA_DIR

    if akasha_config.db_path:
        path = Path(akasha_config.db_path)
        return path if path.is_absolute() else USER_DATA_DIR / path

    return USER_DATA_DIR / "memory" / user_id / "akasha.db"


def _str_value(value: object, default: str) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, int | float):
        return str(value)
    return default


def _int_value(value: object, default: int) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str | float):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    return default


def _float_value(value: object, default: float) -> float:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default
