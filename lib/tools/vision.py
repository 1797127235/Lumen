"""Vision 工具 — 读取并分析图片内容。

支持 PNG、JPEG、GIF、BMP、WebP 格式。大图自动压缩。
"""

from __future__ import annotations

import base64
import io
import os
from pathlib import Path
from typing import Any

from lib.tools._base import ToolDef, ToolMeta, tool_error, tool_ok
from shared.logging import get_logger

logger = get_logger(__name__)

# 限制常量
_MAX_FILE_BYTES = 20 * 1024 * 1024  # 20MB 原始文件上限
_MAX_DATA_URI_BYTES = 8 * 1024 * 1024  # 8MB data URI 上限（base64 编码后）
_MAX_EDGE = 4096  # 最长边像素上限

# 支持的图片格式（魔数检测）
_IMAGE_MAGIC = {
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"\xff\xd8\xff": "image/jpeg",
    b"GIF87a": "image/gif",
    b"GIF89a": "image/gif",
    b"BM": "image/bmp",
    b"RIFF": "image/webp",  # 需要额外检查 WEBP
}


def _detect_mime(file_path: Path) -> str | None:
    """通过文件头魔数检测图片格式"""
    try:
        with open(file_path, "rb") as f:
            header = f.read(12)
    except OSError:
        return None

    for magic, mime in _IMAGE_MAGIC.items():
        if header.startswith(magic):
            if mime == "image/webp" and header[8:12] != b"WEBP":
                continue
            return mime
    return None


def _encode_image_data_uri(file_path: Path) -> str:
    """读取图片并编码为 data URI，大图自动缩放压缩"""
    file_size = os.path.getsize(file_path)
    if file_size > _MAX_FILE_BYTES:
        raise ValueError(
            f"图片文件过大（{file_size / 1024 / 1024:.1f}MB），"
            f"上限为 {_MAX_FILE_BYTES / 1024 / 1024:.0f}MB。"
            "请压缩图片后重试。"
        )

    mime = _detect_mime(file_path)
    if mime is None:
        raise ValueError("不支持的图片格式。仅支持 PNG、JPEG、GIF、BMP、WebP。")

    try:
        from PIL import Image, ImageOps
    except ModuleNotFoundError:
        raise ValueError("当前环境未安装 Pillow，无法处理图片。请安装 Pillow 后重试。")

    # 验证图片完整性
    try:
        with Image.open(file_path) as img:
            img.verify()
    except Exception as e:
        raise ValueError("图片文件无法解码或已损坏。") from e

    # 读取并处理图片
    with Image.open(file_path) as img:
        img = ImageOps.exif_transpose(img)

        # 转换为 RGB
        if img.mode not in ("RGB", "L"):
            canvas = Image.new("RGB", img.size, (255, 255, 255))
            alpha = img.getchannel("A") if "A" in img.getbands() else None
            canvas.paste(img.convert("RGB"), mask=alpha)
            img = canvas
        elif img.mode == "L":
            img = img.convert("RGB")

        # 检查是否需要缩放
        raw = file_path.read_bytes()
        raw_b64_len = len(base64.b64encode(raw).decode())
        if max(img.size) > _MAX_EDGE or raw_b64_len > _MAX_DATA_URI_BYTES:
            img.thumbnail((_MAX_EDGE, _MAX_EDGE))

        # 尝试保存
        if raw_b64_len <= _MAX_DATA_URI_BYTES and max(img.size) <= _MAX_EDGE:
            buf = io.BytesIO()
            if mime == "image/jpeg":
                img.save(buf, format="JPEG", quality=95, optimize=True)
                clean_mime = "image/jpeg"
            else:
                img.save(buf, format="PNG", optimize=True)
                clean_mime = "image/png"
            clean_b64 = base64.b64encode(buf.getvalue()).decode()
            if len(clean_b64) <= _MAX_DATA_URI_BYTES:
                return f"data:{clean_mime};base64,{clean_b64}"

        # 尝试压缩
        best: bytes | None = None
        for quality in (85, 75, 65, 55, 45):
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            candidate = buf.getvalue()
            candidate_b64 = base64.b64encode(candidate).decode()
            best = candidate
            if len(candidate_b64) <= _MAX_DATA_URI_BYTES:
                return f"data:image/jpeg;base64,{candidate_b64}"

    if best is None:
        raise ValueError("图片压缩失败")
    best_b64 = base64.b64encode(best).decode()
    raise ValueError(
        f"图片压缩后仍然过大（{len(best_b64) / 1024 / 1024:.1f}MB base64），"
        f"上限为 {_MAX_DATA_URI_BYTES / 1024 / 1024:.0f}MB。"
        "请继续压缩图片或裁剪到只包含需要分析的区域。"
    )


def _get_vl_config() -> dict[str, Any]:
    """获取 VL 模型配置

    复用 build_llm_call_params() 统一解析逻辑，确保优先级一致。
    """
    from core.config import build_llm_call_params, load_user_config

    user_cfg = load_user_config()

    vl_provider = user_cfg.get("vl_provider") or ""
    vl_model = user_cfg.get("vl_model") or ""

    # 如果 VL 未配置，尝试复用主模型
    if not vl_provider or not vl_model:
        main_params = build_llm_call_params()
        return {
            **main_params,
            "is_main_model": True,
        }

    # 使用 build_llm_call_params 统一解析
    # 优先级（继承自该函数）：vl_api_key → 顶层 llm_api_key → providers[vl_provider].key
    vl_api_key = user_cfg.get("vl_api_key") or ""
    vl_base_url = user_cfg.get("vl_base_url") or ""

    params = build_llm_call_params(
        model=vl_model,
        provider=vl_provider,
        api_key=vl_api_key or None,
        base_url=vl_base_url or None,
    )

    return {
        **params,
        "is_main_model": False,
    }


async def _image_read(args: dict[str, Any], deps) -> Any:
    """Vision 工具主函数

    返回 ToolReturn（通过 tool_ok/tool_error 封装）
    """
    file_path_str = args.get("file_path", "").strip()
    prompt = args.get("prompt", "描述这张图片的内容").strip()

    if not file_path_str:
        return tool_error("请提供 file_path")

    # 解析路径
    from lib.tools.files import _resolve_read

    resolved, err = _resolve_read(file_path_str, str(deps.workspace_root))
    if err:
        return tool_error(err)

    if not os.path.exists(resolved):
        return tool_error(f"文件不存在：{resolved}")

    if not os.path.isfile(resolved):
        return tool_error(f"路径不是文件：{resolved}")

    # 编码图片
    try:
        data_uri = _encode_image_data_uri(Path(resolved))
    except ValueError as e:
        return tool_error(f"图片处理失败：{e}")
    except Exception as e:
        return tool_error(f"读取图片文件失败：{e}")

    # 获取 VL 配置
    vl_config = _get_vl_config()

    if not vl_config["api_key"]:
        return tool_error("未配置 VL 模型 API Key。请在设置页面配置 VL 模型，" "或确保主模型 API Key 已配置。")

    # 调用 VL 模型
    try:
        import litellm

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": data_uri, "detail": "high"},
                    },
                ],
            }
        ]

        response = await litellm.acompletion(
            model=vl_config["model"],
            messages=messages,
            api_key=vl_config["api_key"],
            base_url=vl_config.get("base_url"),
            max_tokens=2048,
        )

        content = response.choices[0].message.content
        if content:
            model_info = f"[使用模型：{vl_config['model']}]"
            if vl_config["is_main_model"]:
                model_info = f"[使用主模型：{vl_config['model']}]"
            return tool_ok(f"{model_info}\n\n{content}")

        return tool_error("视觉模型未返回任何内容，请尝试调整 prompt 后重试。")

    except Exception as e:
        return tool_error(f"调用视觉模型失败：{e}")


def create_vision_tools() -> list[ToolDef]:
    """创建 Vision 工具"""
    return [
        ToolDef(
            name="image_read",
            description="读取并分析图片内容。支持 PNG、JPEG、GIF、BMP、WebP 格式。大图自动压缩。",
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "图片文件路径",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "你想从图片中了解什么（默认：描述图片内容）",
                        "default": "描述这张图片的内容",
                    },
                },
                "required": ["file_path"],
            },
            execute=_image_read,
            read_only=True,
            meta=ToolMeta(
                always_on=True,
                risk="read-only",
                search_hint="图片、image、照片、截图、识别、OCR",
            ),
        )
    ]
