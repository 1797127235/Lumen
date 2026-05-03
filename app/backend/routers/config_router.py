"""用户配置路由 — 读写 ~/.careeros/config.json"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.backend.config import apply_user_config, get_settings, load_user_config, save_user_config

router = APIRouter(tags=["config"])


class ConfigResponse(BaseModel):
    dashscope_api_key: str = ""  # 返回时脱敏
    has_api_key: bool = False


class ConfigUpdate(BaseModel):
    dashscope_api_key: str | None = None


@router.get("/config", response_model=ConfigResponse)
async def get_config() -> ConfigResponse:
    """获取当前用户配置"""
    user_config = load_user_config()
    has_key = bool(user_config.get("dashscope_api_key") or get_settings().dashscope_api_key)
    return ConfigResponse(
        dashscope_api_key="***" if has_key else "",
        has_api_key=has_key,
    )


@router.post("/config", response_model=ConfigResponse)
async def update_config(body: ConfigUpdate) -> ConfigResponse:
    """更新用户配置"""
    data = {}
    if body.dashscope_api_key is not None:
        data["dashscope_api_key"] = body.dashscope_api_key

    if data:
        merged = save_user_config(data)
        apply_user_config(get_settings(), merged)

    return await get_config()
