# VL 模型配置与 Vision 工具方案

> 日期：2026-05-29
> 状态：草案
> 依赖：[统一配置层方案](./unified-config-layer.md)（已完成）

## 背景

Lumen 当前缺少图片读取能力。用户发送图片时，Agent 无法分析图片内容。需要：

1. **VL 模型配置**：让用户配置视觉语言模型（Vision-Language Model）
2. **Vision 工具**：让 Agent 能够读取和分析图片

## 目标

- 用户可在设置页配置 VL 模型（独立于主模型）
- Agent 可调用 `image_read` 工具分析图片
- 支持自动压缩大图（20MB → 8MB base64）
- 支持复用主模型（如果主模型支持多模态）

## 设计方案

### 1. 配置层设计

#### config.json 新增字段

```json
{
  "vl_provider": "dashscope",
  "vl_model": "qwen-vl-max",
  "vl_api_key": "",
  "vl_base_url": ""
}
```

#### 字段说明

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `vl_provider` | string | `""` | VL 模型供应商，空=复用主模型 |
| `vl_model` | string | `""` | VL 模型名称，空=复用主模型 |
| `vl_api_key` | string | `""` | VL API Key，空=复用主模型 key |
| `vl_base_url` | string | `""` | VL Base URL，空=复用主模型 URL |

#### 配置优先级

```
vl_api_key（VL 专用配置）
    ↓ 未配置时
settings.llm_api_key（主模型顶层 key）
    ↓ 未配置时
providers[vl_provider].api_key（供应商页面配置）
```

> **注意**：此顺序即 `build_llm_call_params()` 的实际优先级
> （`api_key or settings.llm_api_key or provider_key`）。复用同一套解析逻辑，
> 不要在 VL 路径自行调换顺序，否则会和主模型出现两套不一致的 key 解析。

### 2. Vision 工具设计

#### 工具定义

```python
# lib/tools/vision.py

ToolDef(
    name="image_read",
    description="读取并分析图片内容。支持 PNG、JPEG、GIF、BMP、WebP 格式。大图自动压缩。",
    input_schema={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "图片文件路径"
            },
            "prompt": {
                "type": "string",
                "description": "你想从图片中了解什么（默认：描述图片内容）",
                "default": "描述这张图片的内容"
            }
        },
        "required": ["file_path"]
    },
    execute=_image_read,
    read_only=True,
    meta=ToolMeta(
        always_on=True,  # 图片场景高频且明确，避免 tool_search 的额外往返
        risk="read-only",
        search_hint="图片、image、照片、截图、识别、OCR"
    )
)
```

#### 可发现性：always_on + system prompt 路由（两者缺一不可）

`always_on=True` 只解决"工具可见、可直接调用",不解决"agent 知道该用它"。
当前 system prompt 的 `[attached_file: {path}]` 指令只指向 `file_read`，agent 看到图片附件仍会先调 file_read（读出二进制乱码）。

因此需要**配套**在 `core/agent.py` 的 `[attached_file]` 指令里加分流（实现计划任务 5）：

```
当 [attached_file] 是图片（.png/.jpg/.jpeg/.gif/.bmp/.webp）时调用 image_read，
其他文件用 file_read。
```

两者结合后，图片附件一步到位走 image_read，不再需要 file_read → 提示 → 重试的多跳。

#### 工具实现

```python
# lib/tools/vision.py

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
    from core.config import get_settings, load_user_config, build_llm_call_params
    
    settings = get_settings()
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
    from lib.tools.files import _resolve
    resolved, err = _resolve(file_path_str, str(deps.workspace_root), allow_session_files=True)
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
        return tool_error(
            "未配置 VL 模型 API Key。请在设置页面配置 VL 模型，"
            "或确保主模型 API Key 已配置。"
        )
    
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
                        "description": "图片文件路径"
                    },
                    "prompt": {
                        "type": "string",
                        "description": "你想从图片中了解什么（默认：描述图片内容）",
                        "default": "描述这张图片的内容"
                    }
                },
                "required": ["file_path"]
            },
            execute=_image_read,
            read_only=True,
            meta=ToolMeta(
                always_on=True,
                risk="read-only",
                search_hint="图片、image、照片、截图、识别、OCR"
            ),
        )
    ]
```

### 3. 配置层实现

#### 设计决策：VL 配置不加 Settings

VL 配置字段（`vl_provider`、`vl_model` 等）**不加入** `Settings(BaseSettings)`，原因：

1. **避免死字段**：`apply_user_config` 的 `_CONFIG_KEYS` 白名单不含 `vl_*`，config.json 里的值不会同步进 Settings
2. **单一读取点**：Vision 工具直接从 `load_user_config()` 读取，与 `_get_vl_config()` 一致
3. **简化实现**：不需要修改 `apply_user_config` 白名单

如果未来需要支持 `.env` 中的 `VL_*` 环境变量，再将 `vl_*` 加入 Settings 并更新白名单。

#### server/routes/config.py

```python
class ConfigResponse(BaseModel):
    # ... 现有字段 ...
    
    # VL 配置
    vl_provider: str = ""
    vl_model: str = ""
    has_vl_key: bool = False

class ConfigUpdate(BaseModel):
    # ... 现有字段 ...
    
    # VL 配置
    vl_provider: str | None = None
    vl_model: str | None = None
    vl_api_key: str | None = None
    vl_base_url: str | None = None
```

#### lib/tools/factory.py

```python
from lib.tools.vision import create_vision_tools

def register_all_tools() -> ToolRegistry:
    # ... 现有代码 ...
    
    all_tools: list[ToolDef] = [
        *create_file_tools(),
        *create_memory_tools(),
        *create_profile_tools(),
        *create_web_tools(),
        *create_web_search_tools(),
        *create_shell_tools(),
        *create_skill_tools(),
        *create_delegate_tools(),
        *create_vision_tools(),  # 新增
        create_tool_search(),
    ]
    
    # ... 现有代码 ...
```

## 实现计划

### 阶段 1：VL 配置层（约 20 分钟）

| 序号 | 任务 | 文件 | 说明 |
|------|------|------|------|
| 1 | Config API 支持 VL | `server/routes/config.py` | ConfigResponse/ConfigUpdate 添加 VL 字段 |
| 2 | TUI API 支持 VL | `channels/cli/cmd/tui/lumen/api.ts` | LumenConfig 类型添加 VL 字段 |

### 阶段 2：Vision 工具（约 40 分钟）

| 序号 | 任务 | 文件 | 说明 |
|------|------|------|------|
| 3 | 创建 vision 工具 | `lib/tools/vision.py` | 实现图片读取 + VL 模型调用 |
| 4 | 注册到工厂 | `lib/tools/factory.py` | 导入并注册 vision 工具 |
| 5 | system prompt 图片路由 | `core/agent.py` | 在 `[attached_file]` 指令里加分流：图片扩展名用 `image_read`，其他用 `file_read` |
| 6 | （兜底）修改 file_read | `lib/tools/files.py` | file_read 读到图片时提示改用 image_read；有了任务 5 后这步退化为防御性兜底，可选 |

### 阶段 3：前端 UI（可选，约 30 分钟）

| 序号 | 任务 | 文件 | 说明 |
|------|------|------|------|
| 7 | 设置页添加 VL 配置 | `src/components/providers/` | VL 供应商选择、模型名称 |
| 8 | TUI 添加 /vl 命令 | `server/routes/commands.py` | 快速配置 VL 模型 |

## 测试场景

### 场景 1：配置 VL 模型

```bash
# 1. 在设置页配置 VL 模型
vl_provider: dashscope
vl_model: qwen-vl-max

# 2. 发送图片 + 提问
"这张图片里有什么？"

# 3. 验证 Agent 调用 image_read 工具
```

### 场景 2：复用主模型

```bash
# 1. 不配置 VL 模型（留空）

# 2. 主模型支持多模态（如 GPT-4o）

# 3. 发送图片 + 提问

# 4. 验证使用主模型分析图片
```

### 场景 3：大图自动压缩

```bash
# 1. 发送 20MB 的图片

# 2. 验证自动压缩到 8MB 以下

# 3. 验证图片内容可识别
```

### 场景 4：不支持的格式

```bash
# 1. 发送 .bmp 或 .tiff 文件

# 2. 验证返回友好错误信息
```

## 依赖项

### Python 依赖

```
# requirements.txt
Pillow>=10.0.0  # 图片处理
litellm>=1.0.0  # VL 模型调用
```

### 可选依赖

```
# 如果需要 OCR 能力
# pytesseract>=0.3.10
# tesseract-ocr 系统依赖
```

## 设计权衡

### 1. "复用主模型"是独立调用，不是让主 Agent 直接看图

当 VL 未配置时，Vision 工具会复用主模型的配置（provider/model/api_key），但仍然是**独立发起一次带图片的请求**，而非让主 Agent 直接看到图片。

**原因**：
- 工具自洽：Vision 工具是独立的，不依赖主 Agent 的多模态能力
- 文本模型也能用：即使主模型不支持多模态，只要配置了支持多模态的 VL 模型就能工作
- 简化实现：不需要修改主 Agent 的消息结构

**权衡**：
- ✅ 优点：工具独立、兼容性好
- ❌ 缺点：丢失对话上下文，VL 模型只看到单张图片 + 固定 prompt

**未来优化**：如果需要主 Agent 直接看图（保留上下文），需要修改 `agent_runner.py`，将图片作为 content block 注入到用户消息中，而不是通过工具调用。

### 2. 与 session_files.py is_image() 的关系

- `lib/chat/session_files.py:108 is_image()`：通过扩展名判断是否为图片，用于会话附件管理
- `lib/tools/vision.py _detect_mime()`：通过魔数检测图片格式，用于生成 data URI

两者用途不同，不算冗余。

### 3. always_on=True 的设计决策

`image_read` 设置为 `always_on=True`，原因：
- 图片场景高频且明确（用户发图 → Agent 需要分析）
- 避免 `tool_search` 的额外往返（至少 3 个来回：file_read → 提示 → tool_search → 加载 → 调用）
- 与 `[attached_file: x.png]` 系统提示配合，Agent 可直接调用

1. **OCR 增强**：集成 Tesseract 或云 OCR 服务，提取图片中的文字
2. **多图支持**：一次调用分析多张图片
3. **图片生成**：集成 DALL-E、Stable Diffusion 等模型
4. **视频分析**：扩展支持视频文件的关键帧提取和分析

## 参考实现

- [akashic-agent vision.py](https://github.com/nicepkg/akashic-agent/blob/main/agent/tools/vision.py) - 参考的 VL 工具实现
- [akashic-agent filesystem.py](https://github.com/nicepkg/akashic-agent/blob/main/agent/tools/filesystem.py) - 图片检测和压缩逻辑

## 参考文档

- [统一配置层方案](./unified-config-layer.md) - 前置依赖
- [core/config.py](../../core/config.py) - 配置管理
- [lib/tools/factory.py](../../lib/tools/factory.py) - 工具注册
