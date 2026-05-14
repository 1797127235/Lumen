"""工具运行时工厂 — 组装 Registry + Dispatcher + Toolsets。"""

from __future__ import annotations

from backend.modules.agent.tools.builtin import (
    handle_data_source_get_item,
    handle_data_source_list,
    handle_data_source_search,
    handle_data_source_status,
    handle_file_list,
    handle_file_read,
    handle_file_search,
    handle_file_write,
    handle_get_profile,
    handle_memory_save,
    handle_memory_search,
    handle_update_profile,
)
from backend.modules.agent.tools.core import (
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
                "profile / emotions / reference / chat / knowledge；不传则搜索全部。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词或时间描述"},
                    "scope": {
                        "type": "string",
                        "description": "搜索范围 — profile / emotions / reference / chat / knowledge",
                    },
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

    # ── 注册数据源读取工具 ──
    registry.register(
        ToolDefinition(
            name="data_source_search",
            description=(
                "搜索用户已连接的数据源（Obsidian 笔记、Markdown 文件等）。"
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
            handler=handle_data_source_search,
        )
    )

    registry.register(
        ToolDefinition(
            name="data_source_list",
            description="列出当前用户已连接的数据源及其状态。",
            input_schema={"type": "object", "properties": {}},
            category="builtin",
            read_only=True,
            handler=handle_data_source_list,
        )
    )

    registry.register(
        ToolDefinition(
            name="data_source_get_item",
            description="按 item_id 读取外部文档的完整内容，用于用户追问某一条资料。",
            input_schema={
                "type": "object",
                "properties": {
                    "item_id": {"type": "string", "description": "文档 item_id"},
                    "max_chars": {"type": "integer", "description": "最大返回字符数", "default": 4000},
                },
                "required": ["item_id"],
            },
            category="builtin",
            read_only=True,
            handler=handle_data_source_get_item,
        )
    )

    registry.register(
        ToolDefinition(
            name="data_source_status",
            description="诊断数据源同步状态，展示各数据源的文档数和最近错误。",
            input_schema={"type": "object", "properties": {}},
            category="builtin",
            read_only=True,
            handler=handle_data_source_status,
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

    resolver.register(
        "chat-core",
        ToolsetConfig(
            description="核心对话工具",
            tools=["memory_search", "memory_save", "get_profile", "update_profile"],
        ),
    )

    resolver.register(
        "data-source-read",
        ToolsetConfig(
            description="数据源读取工具",
            tools=["data_source_search", "data_source_list", "data_source_get_item", "data_source_status"],
        ),
    )

    resolver.register(
        "default-chat",
        ToolsetConfig(
            description="默认对话配置",
            includes=["chat-core", "data-source-read", "file"],
        ),
    )

    # ── 注册 DocumentIndexProvider 动态工具（覆盖同名 built-in）──
    _register_provider_tools(registry, resolver)

    dispatcher = ToolDispatcher(registry)
    return registry, dispatcher, resolver


def _register_provider_tools(registry: ToolRegistry, resolver: ToolsetResolver) -> None:
    """从当前激活的 DocumentIndexProvider 获取工具并注册到 Registry。

    Provider 工具会覆盖同名 built-in 工具，实现 Hermes 对齐的可插拔搜索后端。
    """
    from backend.modules.data_sources.ingestion import get_document_index_provider

    provider = get_document_index_provider()
    if provider is None:
        return

    schemas = provider.get_tool_schemas()
    if not schemas:
        return

    provider_tools: list[str] = []
    for schema in schemas:
        name = schema.get("name", "")
        if not name:
            continue

        # 如果同名 built-in 已存在，先注销（Provider 优先）
        if registry.has(name):
            registry.unregister(name)

        # 创建代理 handler：运行时委托给 provider.handle_tool_call
        async def _provider_handler(args, ctx, _name=name, _provider=provider):
            return await _provider.handle_tool_call(_name, args)

        registry.register(
            ToolDefinition(
                name=name,
                description=schema.get("description", f"{provider.name} 提供的 {name}"),
                input_schema=schema.get("parameters", {"type": "object", "properties": {}}),
                category="provider",
                read_only=True,
                handler=_provider_handler,
            )
        )
        provider_tools.append(name)

    # 将 Provider 工具加入 data-source-read toolset
    if provider_tools:
        ds_tools = resolver.get_toolset("data-source-read")
        ds_tools.update(provider_tools)
        resolver.register(
            "data-source-read",
            ToolsetConfig(
                description="数据源读取工具（含 Provider 动态工具）",
                tools=list(ds_tools),
            ),
        )
