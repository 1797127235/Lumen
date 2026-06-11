from __future__ import annotations

from typing import Any

from lib.tools._base import ToolDef, ToolMeta, tool_ok


async def _handle_result_read(args: dict[str, Any], ctx: Any = None) -> str:
    from lib.chat.context_budget import ToolResultStore

    conv_id = args.get("conversation_id")
    if not conv_id:
        return "❌ 无法确定会话 ID"

    store = ToolResultStore(conv_id)
    result_id = args["result_id"]
    content = store.load(result_id)
    if not content:
        return f"❌ 未找到结果 {result_id}，可能已过期"

    offset = args.get("offset", 0)
    limit = min(args.get("limit", 8000), 20_000)
    chunk = content[offset : offset + limit]

    if offset + limit < len(content):
        chunk += f"\n\n... (还有 {len(content) - offset - limit:,} 字符未读取，调整 offset 继续读取)"
    return tool_ok(chunk)


def create_result_read_tool() -> ToolDef:
    return ToolDef(
        name="result_read",
        description=(
            "读取之前因过大而落盘的工具返回完整内容。"
            "当你在上下文中看到 <persisted-output> 标签时，"
            "可使用此工具读取完整结果。支持 offset/limit 分段读取。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "result_id": {
                    "type": "string",
                    "description": "persisted-output 标签中的 result_id",
                },
                "offset": {
                    "type": "integer",
                    "description": "起始字符位置（默认 0）",
                    "default": 0,
                },
                "limit": {
                    "type": "integer",
                    "description": "读取字符数（默认 8000，最大 20000）",
                    "default": 8000,
                },
            },
            "required": ["result_id"],
        },
        execute=_handle_result_read,
        meta=ToolMeta(risk="read-only", always_on=True),
    )
