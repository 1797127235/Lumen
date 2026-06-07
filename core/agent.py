"""Lumen Agent — 核心编排，对应 openhanako core/agent.js"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai import Agent, RunContext
from pydantic_ai.capabilities import ProcessHistory
from pydantic_ai.messages import (  # pyright: ignore[reportMissingImports]
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.openai import OpenAIChatModel
from sqlalchemy.ext.asyncio import AsyncSession  # pyright: ignore[reportMissingImports]

from core.config import get_settings, load_user_config
from shared.logging import get_logger

logger = get_logger(__name__)


# ════════════════════════════
#  消息历史处理器
# ════════════════════════════
def _clean_orphaned_tool_parts(messages: list[ModelMessage]) -> list[ModelMessage]:
    """清理孤立的工具消息，确保每个 tool-return / retry-prompt 都有对应的 tool-call。

    DeepSeek 等严格 API 要求 role='tool' 的消息必须紧跟在含 tool_calls 的 assistant 消息之后。
    压缩、序列化/反序列化、PydanticAI 内部消息合并都可能产生孤立。

    策略：
    1. 收集所有 ModelResponse 中 ToolCallPart 的 tool_call_id
    2. 从 ModelRequest 中移除没有对应 tool-call 的 ToolReturnPart / RetryPromptPart
    3. 移除只含 ToolCallPart 但后续无 ToolReturnPart 的尾部 ModelResponse
    """
    # --- Pass 1: 收集所有有效的 tool_call_id ---
    valid_call_ids: set[str] = set()
    for msg in messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart) and part.tool_call_id:
                    valid_call_ids.add(part.tool_call_id)

    # --- Pass 2: 过滤 ModelRequest 中的孤立 tool-return / retry-prompt ---
    result: list[ModelMessage] = []
    for msg in messages:
        if not isinstance(msg, ModelRequest):
            result.append(msg)
            continue

        has_tool_parts = any(isinstance(p, ToolReturnPart | RetryPromptPart) for p in msg.parts)
        if not has_tool_parts:
            result.append(msg)
            continue

        # 保留非 tool-return 的 parts + 有匹配 tool_call_id 的 parts
        kept: list = []
        for p in msg.parts:
            if isinstance(p, ToolReturnPart | RetryPromptPart):
                if p.tool_call_id and p.tool_call_id in valid_call_ids:
                    kept.append(p)
                # 无匹配的 → 丢弃（这就是孤立的部分）
            else:
                kept.append(p)

        if kept:
            from dataclasses import replace

            result.append(replace(msg, parts=kept))
        # 全部被过滤 → 跳过整条消息

    # --- Pass 3: 移除末尾只有 ToolCallPart 但无后续 ToolReturnPart 的 ModelResponse ---
    while result:
        last = result[-1]
        if not isinstance(last, ModelResponse):
            break
        parts = last.parts
        # 如果这条 Response 只有 ToolCallPart（无 TextPart），且后续没有对应的 Return
        has_text = any(type(p).__name__ == "TextPart" for p in parts)
        has_calls = any(isinstance(p, ToolCallPart) for p in parts)
        if has_calls and not has_text:
            # 纯工具调用响应，但无后续返回 → 移除
            result.pop()
        else:
            break

    removed_count = len(messages) - len(result)
    if removed_count > 0:
        logger.debug(
            "ProcessHistory 清理了孤立工具消息",
            original=len(messages),
            cleaned=len(result),
            removed=removed_count,
        )

    return result


# ════════════════════════════
#  依赖注入类型
# ════════════════════════════
@dataclass
class LumenDeps:
    """PydanticAI RunContext 依赖，贯穿整个 agent run。"""

    user_id: str
    db: AsyncSession
    conversation_id: str | None = None
    current_user_input: str | None = None
    pending_event_ids: list[str] = field(default_factory=list, repr=False, compare=False)
    build_context_cache: str = field(default="", repr=False, compare=False)
    agent_generation: int = 0
    tool_state: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)
    usage_budget: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)
    trace_sink: list[dict] = field(default_factory=list, repr=False, compare=False)
    workspace_root: Any = field(default=None, repr=False, compare=False)
    source_platform: str = "web"
    progress_emitter: Callable[[str, str], None] | None = field(default=None, repr=False, compare=False)


# ════════════════════════════
#  Agent 类
# ════════════════════════════
class LumenAgent:
    """Lumen agent 实例，按 (provider, model, tools) 缓存多个 Agent。"""

    def __init__(self) -> None:
        self._agents: dict[str, Agent[LumenDeps, str]] = {}
        self._generation: int = 0
        self._config_fingerprint: str = ""

    # ────────────────────────
    #  公开接口
    # ────────────────────────
    def _get_config_fingerprint(self) -> str:
        """计算配置指纹，变更时自动失效缓存

        包含：
        - Settings 中的顶层 key（兼容 .env）
        - config.json 中的 providers 配置（供应商页面配置）
        """
        settings = get_settings()
        user_cfg = load_user_config()

        # 注意：dashscope_api_key 已从 Settings 移除，只在迁移逻辑中使用
        parts = [
            settings.llm_api_key,
            settings.llm_base_url,
            # 关键：包含 providers 配置，否则供应商页面改 key 不会触发缓存失效
            json.dumps(user_cfg.get("providers") or {}, sort_keys=True),
        ]
        return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]

    def get(self) -> Agent[LumenDeps, str]:
        """返回当前 config 对应的缓存 Agent；config/工具变化时清旧缓存。

        模型选择的唯一真相源是 config（get_settings），不接受任何请求级覆盖。
        """
        s = get_settings()
        eff_provider = s.llm_provider
        eff_model = s.llm_model
        tools_fp = self._tool_fingerprint()
        key = f"{eff_provider}|{eff_model}|{tools_fp}"

        # 配置变更时清空所有缓存
        current_fp = self._get_config_fingerprint()
        if current_fp != self._config_fingerprint:
            logger.info("配置变更，清空 Agent 缓存")
            self._agents.clear()
            self._config_fingerprint = current_fp

        if key not in self._agents:
            from lib.tools.factory import register_all_tools

            register_all_tools()
            self._agents = {k: v for k, v in self._agents.items() if k.endswith(tools_fp)}
            self._agents[key] = self.create(eff_provider, eff_model)
            self._generation += 1
            logger.info("Agent 已构建", provider=eff_provider, model=eff_model, generation=self._generation)

        return self._agents[key]

    def build_system_prompt(self) -> str:
        """组装静态 system prompt（对应 openhanako buildSystemPrompt 静态前缀）。

        静态内容放前面，动态内容（记忆、时间戳）由 @agent.system_prompt 装饰器追加在末尾，
        最大化跨 session 的 KV cache 命中率。
        """

        def section(title: str, content: str) -> str:
            return f"\n\n---\n\n{title}\n\n{content}"

        parts = [
            "你是「Lumen」，用户的 AI 伙伴。说话像一个真正认识用户的朋友。",
        ]

        parts.append(
            section(
                "## 工具使用",
                "用户分享个人信息时立即用 update_profile / memory_save 保存；"
                "需要回忆时用 memory_search。\n"
                "搜不到如实说，别编；搜完空结果也要告诉用户「没找到相关内容」，不要沉默。\n"
                "需要调用工具时直接调用，不要在回复中解释你的调用计划、工具状态或加载过程。"
                "用户不需要知道你要调用什么工具、为什么调用、工具是否可见。"
                "如果某个操作需要用户等待，最多说一句'稍等'。\n\n"
                "当用户消息中包含 `[attached_file: {path}]` 标记时，如果你需要了解该文件内容，"
                "请根据文件类型选择合适的工具：\n"
                "- 图片文件（.png/.jpg/.jpeg/.gif/.bmp/.webp）：使用 `image_read` 工具\n"
                "- 其他文件：使用 `file_read` 工具\n"
                "传入 `file_path` 参数。\n"
                "如需执行系统命令（如安装依赖、运行测试、查看进程等），使用 `shell` 工具；"
                "长时间运行的命令可设 run_in_background=true，之后用 task_output 查看结果。\n"
                "如果当前需要的工具不在可见列表中，使用 `tool_search` 搜索并加载，"
                "加载后的工具在当前对话的下一步即可直接调用。\n"
                "遇到深度调研、多步搜索、需要大量工具调用的任务（如「帮我调研 X」「搜集 Y 的资料」），"
                "优先用 `delegate` 委派给子 Agent：把明确的 goal 和必要 context 传过去，"
                "子任务在隔离上下文里跑、不占用主对话，完成后只把结果摘要交回。"
                "不要自己在主对话里一步步搜索、读网页地堆调用。\n"
                "**工具发现与加载是内部行为，禁止向用户提及。** "
                "用户只需要结果，不需要知道你是怎么获得工具的。",
            )
        )

        parts.append(
            section(
                "## 对话风格",
                "开场白简短自然，不罗列功能。回复直接开始，不要以逗号或其他标点符号打头。",
            )
        )

        parts.append(
            section(
                "## 记忆使用",
                "记忆和用户档案是你内化的背景知识，你和用户是认识很久的人，这些事你本来就知道。"
                "记忆的存在感应该是零，它的作用应该是满的。\n\n"
                "- **只有当用户提到了相关内容，记忆才参与进来。** 用户没有提起的话题，不要主动从记忆里翻出来讲。"
                "记忆参与的方式是无声的：影响你的角度、语气、判断，但不出现在文字里。\n"
                "- **永远不要让用户感觉到「记忆」这个东西的存在。** "
                "禁止使用「我记得」「你之前说过」「根据记忆」这类表述，除非用户主动问「你还记得 xxx 吗」。\n"
                "- **记忆可能过时，当前对话永远优先。** 信息冲突时以对话为准，不要用旧记忆纠正用户。\n"
                "- **发现记忆矛盾时立即静默清理。** 如果你发现记忆中的待办/计划/意图已经被完成（如用户说「已经订阅了」「搞定了」），"
                "立即调用 memory(action='remove', old_text='旧内容') 删除过时条目，不要问用户确认。"
                "清理是内部 housekeeping，不需要告诉用户你做了什么。",
            )
        )

        return "".join(parts)

    def create(
        self,
        provider: str | None = None,
        model: str | None = None,
    ) -> Agent[LumenDeps, str]:
        """创建一个新的 PydanticAI Agent 实例。

        工具通过 @agent.toolset 动态注册，每个 run step 前重新评估，
        使 tool_search 解锁的工具在同一轮对话的下一步即可使用。
        """
        model = self._create_model(provider, model)

        from pydantic_ai.capabilities.reinject_system_prompt import ReinjectSystemPrompt

        # system prompt 完全静态 — 动态内容（记忆、时间戳、deferred 工具目录）
        # 由 service 层注入为 user message。
        # ReinjectSystemPrompt：message_history 非空时 PydanticAI 默认跳过 system 注入，
        # 这个 capability 确保每次请求都带上 system prompt。
        agent = Agent(
            model=model,
            deps_type=LumenDeps,
            output_type=str,
            system_prompt=self.build_system_prompt(),
            retries=2,
            end_strategy="graceful",
            capabilities=[
                ReinjectSystemPrompt(),
                ProcessHistory(_clean_orphaned_tool_parts),
            ],
        )

        # 每个 run step 前重新评估，tool_search 更新缓存后下一步立即生效
        @agent.toolset
        async def _dynamic_toolset(ctx: RunContext[LumenDeps]):
            from lib.tools.factory import build_pydantic_toolset_for_conversation

            return build_pydantic_toolset_for_conversation(ctx.deps.conversation_id)

        # 动态 system prompt 尾部：技能目录 + 激活技能内容
        # 稳定前缀（build_system_prompt）始终命中 KV cache；
        # 此函数追加在末尾，仅在有技能时才产生额外 token。
        @agent.system_prompt
        async def _skills_prompt(ctx: RunContext[LumenDeps]) -> str:
            from lib.skills import get_skills_loader

            loader = get_skills_loader()
            summary = loader.build_skills_summary()
            if summary:
                return f"## 可用技能目录\n\n{summary}"
            return ""

        return agent

    @property
    def generation(self) -> int:
        """当前 Agent 代际号，每次重建递增。"""
        return self._generation

    # ────────────────────────
    #  内部方法
    # ────────────────────────

    def _create_model(
        self,
        provider: str | None = None,
        model: str | None = None,
    ) -> OpenAIChatModel:
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider

        settings = get_settings()
        provider = provider or settings.llm_provider
        model_name = model or settings.llm_model

        # 优先从 providers 配置读取 key 和 base_url
        user_cfg = load_user_config()
        provider_cfg = (user_cfg.get("providers") or {}).get(provider, {})
        provider_key = provider_cfg.get("api_key", "")
        provider_base_url = provider_cfg.get("base_url", "")

        # 解析优先级：providers 配置 > Settings
        api_key = provider_key or settings.llm_api_key or ""
        base_url = provider_base_url or settings.llm_base_url or ""

        if not api_key:
            raise ValueError("未配置 LLM API Key。请在设置页面配置 API Key，" "或在 .env 文件中设置 LLM_API_KEY。")
        if not base_url:
            raise ValueError(
                f"未配置 LLM Base URL。请在设置页面配置 Base URL，"
                f"或在 .env 文件中设置 LLM_BASE_URL（当前 provider: {provider}）。"
            )

        logger.info("创建模型", provider=provider, model=model_name, base_url=base_url, has_key=bool(api_key))

        return OpenAIChatModel(
            model_name,
            provider=OpenAIProvider(base_url=base_url, api_key=api_key),
        )

    def _tool_fingerprint(self) -> str:
        """计算已连接 MCP 工具的指纹；新增/删除 MCP server 时 Agent 自动重建。"""
        try:
            from lib.tools.mcp.client_manager import get_mcp_manager

            mcp_tools: list[str] = []
            for server_name, tools in get_mcp_manager().discover_tools():
                for t in tools:
                    mcp_tools.append(f"{server_name}:{t['name']}")
            return hashlib.sha256("|".join(sorted(mcp_tools)).encode()).hexdigest()[:16]
        except Exception:
            return "v1"


# ════════════════════════════
#  模块级单例 + 便捷函数
# ════════════════════════════

_lumen_agent = LumenAgent()


def get_agent() -> Agent[LumenDeps, str]:
    return _lumen_agent.get()


def get_agent_generation() -> int:
    return _lumen_agent.generation


def build_worker_agent(
    tool_names: list[str],
    system_prompt: str,
) -> Agent[LumenDeps, str]:
    """构建一个无状态 worker Agent，用于 delegate 子任务。

    - 复用全局模型配置（同一套 provider/model）
    - 固定受限 toolset（不走 conversation 动态发现）
    - 不注入 Lumen 人格、记忆快照、技能目录
    """
    from pydantic_ai.capabilities.reinject_system_prompt import ReinjectSystemPrompt

    model = _lumen_agent._create_model()

    from lib.tools._registry import get_tool_registry
    from lib.tools.factory import build_pydantic_toolset

    registry = get_tool_registry()
    tools = [registry.get_tool(n) for n in tool_names]
    tools = [t for t in tools if t is not None]
    fixed_toolset = build_pydantic_toolset(tools)

    agent = Agent(
        model=model,
        deps_type=LumenDeps,
        output_type=str,
        system_prompt=system_prompt,
        retries=2,
        end_strategy="graceful",
        capabilities=[
            ReinjectSystemPrompt(),
            ProcessHistory(_clean_orphaned_tool_parts),
        ],
    )

    @agent.toolset
    async def _worker_toolset(ctx):
        return fixed_toolset

    return agent
