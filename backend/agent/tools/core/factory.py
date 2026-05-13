"""工具运行时工厂 — 组装 Registry + Dispatcher + Toolsets。"""

from __future__ import annotations

from backend.agent.tools.builtin import (
    handle_file_list,
    handle_file_read,
    handle_file_search,
    handle_file_write,
    handle_get_profile,
    handle_memory_save,
    handle_memory_search,
    handle_search_external_docs,
    handle_update_profile,
)
from backend.agent.tools.core import (
    ToolDefinition,
    ToolDispatcher,
    ToolRegistry,
    ToolsetConfig,
    ToolsetResolver,
)


def create_tool_runtime() -> tuple[ToolRegistry, ToolDispatcher, ToolsetResolver]:
    """创建完整的工具运行时。

    Returns:
        (registry, dispatcher, resolver)
    """
    from backend.config import get_settings

    registry = ToolRegistry()
    resolver = ToolsetResolver()

    # ── 注册文件工具 ──
    registry.register(
        ToolDefinition(
            name="file_read",
            description="读取文本文件内容，返回带行号的文本。支持分页读取大文件。",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径（相对或绝对）"},
                    "offset": {"type": "integer", "description": "起始行号", "default": 1},
                    "limit": {"type": "integer", "description": "最大行数", "default": 500},
                },
                "required": ["path"],
            },
            category="builtin",
            read_only=True,
            handler=handle_file_read,
        )
    )

    registry.register(
        ToolDefinition(
            name="file_write",
            description="写入或覆盖文本文件。会自动创建父目录。",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "content": {"type": "string", "description": "文件内容"},
                },
                "required": ["path", "content"],
            },
            category="builtin",
            read_only=False,
            requires_approval=True,
            handler=handle_file_write,
        )
    )

    registry.register(
        ToolDefinition(
            name="file_list",
            description="列出目录内容，显示文件和子目录。",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目录路径（可选，默认当前目录）"},
                },
            },
            category="builtin",
            read_only=True,
            handler=handle_file_list,
        )
    )

    registry.register(
        ToolDefinition(
            name="file_search",
            description="在目录下递归搜索文件名匹配正则表达式的文件。",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "搜索目录（可选，默认当前目录）"},
                    "pattern": {"type": "string", "description": "正则表达式模式"},
                },
                "required": ["pattern"],
            },
            category="builtin",
            read_only=True,
            handler=handle_file_search,
        )
    )

    # ── 注册记忆工具 ──
    registry.register(
        ToolDefinition(
            name="memory_search",
            description=(
                "搜索记忆。"
                "search_mode 选择："
                '- "keyword"（默认）— 关键词搜索，适用于「Python」「实习」等具体词；'
                '- "grep" — 时间范围浏览，适用于「最近做了什么」「这周」等自然语言，'
                "必须配合 time_filter 使用。"
                "time_filter（仅 grep 模式生效）："
                'today / yesterday / recent_3d / recent_7d / recent_30d / "YYYY-MM-DD~YYYY-MM-DD"'
                "scope（仅 keyword 模式生效）："
                "profile / emotions / reference / chat；不传则搜索全部。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词或时间描述"},
                    "scope": {"type": "string", "description": "搜索范围 — profile / emotions / reference / chat"},
                    "search_mode": {"type": "string", "description": "keyword（默认）或 grep", "default": "keyword"},
                    "time_filter": {
                        "type": "string",
                        "description": "时间过滤 — today / yesterday / recent_7d 等（仅 grep 模式）",
                    },
                },
                "required": ["query"],
            },
            category="builtin",
            read_only=True,
            handler=handle_memory_search,
        )
    )

    registry.register(
        ToolDefinition(
            name="memory_save",
            description=(
                "保存记忆。主动调用！不要等用户要求！"
                "entity_type: skills / experiences / preferences / goals / decisions / status"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "entity_type": {
                        "type": "string",
                        "description": "类型 — skills / experiences / preferences / goals / decisions / status",
                    },
                    "section": {"type": "string", "description": "标题/名称"},
                    "content": {"type": "string", "description": "具体内容"},
                },
                "required": ["entity_type", "section", "content"],
            },
            category="builtin",
            read_only=False,
            handler=handle_memory_save,
        )
    )

    # ── 注册画像工具 ──
    registry.register(
        ToolDefinition(
            name="get_profile",
            description="获取用户完整画像。通常无需主动调用，画像已在 system prompt 中。",
            input_schema={"type": "object", "properties": {}},
            category="builtin",
            read_only=True,
            handler=handle_get_profile,
        )
    )

    registry.register(
        ToolDefinition(
            name="update_profile",
            description=(
                "更新用户画像。只传有值的字段。"
                "可用字段: school_name, major, grade, graduation_year, school_level, "
                "target_direction, target_company_level, city, gpa, ranking, awards, bio, "
                "english_level, expected_salary"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "school_name": {"type": "string"},
                    "major": {"type": "string"},
                    "grade": {"type": "string"},
                    "graduation_year": {"type": "string"},
                    "school_level": {"type": "string"},
                    "target_direction": {"type": "string"},
                    "target_company_level": {"type": "string"},
                    "city": {"type": "string"},
                    "gpa": {"type": "string"},
                    "ranking": {"type": "string"},
                    "awards": {"type": "array", "items": {"type": "string"}},
                    "bio": {"type": "string"},
                    "english_level": {"type": "string"},
                    "expected_salary": {"type": "string"},
                },
            },
            category="builtin",
            read_only=False,
            handler=handle_update_profile,
        )
    )

    # ── 注册外部文档工具（条件注册）──
    settings = get_settings()
    if settings.external_data_enabled:
        registry.register(
            ToolDefinition(
                name="search_external_docs",
                description=(
                    "搜索用户本地文档（Obsidian 笔记、Markdown 文件等）。"
                    "当用户提到某个技术、项目或想法，但对话记忆中找不到时，"
                    "可用此工具搜索外部笔记。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "搜索关键词"},
                        "limit": {"type": "integer", "description": "最多返回条数", "default": 5},
                    },
                    "required": ["query"],
                },
                category="builtin",
                read_only=True,
                handler=handle_search_external_docs,
            )
        )

    # ── 定义 toolsets ──
    resolver.register(
        "file",
        ToolsetConfig(
            description="文件系统工具",
            tools=["file_read", "file_write", "file_list", "file_search"],
        ),
    )

    # chat-core toolset 改为条件构建
    chat_core_tools = ["memory_search", "memory_save", "get_profile", "update_profile"]
    if settings.external_data_enabled:
        chat_core_tools.append("search_external_docs")

    resolver.register(
        "chat-core",
        ToolsetConfig(
            description="核心对话工具",
            tools=chat_core_tools,
        ),
    )

    resolver.register(
        "default-chat",
        ToolsetConfig(
            description="默认对话配置",
            includes=["chat-core", "file"],
        ),
    )

    dispatcher = ToolDispatcher(registry)
    return registry, dispatcher, resolver
