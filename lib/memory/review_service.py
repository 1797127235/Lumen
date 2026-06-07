"""后台记忆审查服务。

当 Agent 在对话中未主动保存记忆时，后台 fork 一个专用的审查 Agent 审查本轮对话，
判断是否有值得保存的用户信息。

审查 Agent 使用独立的精简 prompt + 受限工具集（仅 memory / focus_update），
不携带 Lumen 人格、记忆快照、技能目录，确保审查是客观的事实记录。
"""

from __future__ import annotations

from shared.logging import get_logger

logger = get_logger(__name__)

_REVIEW_SYSTEM_PROMPT = (
    "你是记忆审查员。你的唯一任务是判断对话中是否有值得持久化的用户信息。\n\n"
    "【核心规则】\n"
    "- 忠实记录用户原意。用户说「已订阅」就记「已订阅」，绝不能记成「想订阅」。\n"
    "- 区分事实（用户陈述的状态）和意图（用户的请求），都准确记录。\n"
    "- 只保存用户明确透露的信息，不推断、不引申、不过度解读。\n"
    "- 宁可漏记也不要误记。不确定时不要保存。\n\n"
    "【target 选择规则】\n"
    "选择 target 的唯一标准是：信息是关于「用户是谁/喜欢什么」（画像）还是「用户做了什么/环境事实」（事实）。\n\n"
    "- target='memory'（事实）: 客观记录用户的行为、状态、已完成的动作、环境信息、项目约定。\n"
    "  包括：已订阅的服务、已完成的任务、当前使用的工具、技术栈、项目配置、学到的教训。\n"
    "  判断标准：如果信息换成另一个用户也成立（客观事实），用 memory。\n\n"
    "- target='user'（画像）: 记录用户的偏好、习惯、性格、价值观、沟通风格。\n"
    "  包括：喜欢的风格、审美取向、工作习惯、性格特点、社交偏好、价值观。\n"
    "  判断标准：如果信息换成另一个用户就不成立（个人特质），用 user。\n\n"
    "【示例】\n"
    "- '已订阅 Simon Willison 的博客' → memory(target='memory', category='fact') [客观事实]\n"
    "- '喜欢简洁的 UI 风格' → memory(target='user', category='preference') [个人偏好]\n"
    "- '正在学习 Rust' → memory(target='memory', category='intent') [当前行为/计划]\n"
    "- '讨厌冗长的回复' → memory(target='user', category='preference') [个人偏好]\n\n"
    "【分类标签】\n"
    "新增记忆时必须选择正确的 category：\n"
    "- 'fact': 稳定事实 — 用户名、职业、已订阅的服务、拥有的设备、完成的事情\n"
    "- 'preference': 长期偏好 — 喜欢的风格、沟通方式、价值观、审美取向\n"
    "- 'intent': 意图/计划 — 想订阅、打算学、准备做、计划中\n"
    "- 'transient': 临时状态 — 最近加班、这周在赶、目前在做\n"
    "- 'correction': 纠正旧记忆 — 用户说「不是那样的」「其实是…」「已经…了」\n"
    "不传默认 fact。\n\n"
    "【矛盾检测】\n"
    "如果用户的新信息与现有记忆矛盾（如之前记「想订阅」现在说「已订阅」），"
    "用 memory(action='replace', old_text='旧记忆关键词', content='新内容', category='fact') 替换旧条目，"
    "不要追加新条目造成重复。\n\n"
    "【完成状态自动清理】\n"
    "检测以下完成信号，发现后立即删除对应的旧待办/意图：\n"
    "- 之前记「想订阅」「计划做」「准备学」，现在用户说「已经订阅了」「做完了」「学会了」→ 立即删除旧 intent\n"
    "- 之前记「未完成」「待办」，现在用户说「已完成」「搞定了」→ 立即删除旧条目\n"
    "- 删除时用 memory(action='remove', old_text='旧记忆关键词')，不需要添加新条目\n\n"
    "【触发保存的条件】\n"
    "1. 用户透露了关于自己的新信息（偏好、习惯、经历、技能、状态变化）\n"
    "2. 用户纠正了之前的认知（明确说「不是那样的」「其实…」）\n"
    "3. 用户提到了正在关注的话题或项目 → 调用 focus_update\n\n"
    "【不保存的情况】\n"
    "- 用户只是提问或请求执行某个操作（没有透露个人信息）\n"
    "- 对话中的闲聊、玩笑、寒暄\n"
    "- 你不确定用户是否真的想被记住的信息\n\n"
    "如果没有值得保存的信息，回复「无需保存」。\n\n"
    "【当前记忆内容】\n"
    "{current_memory}\n\n"
    "【对话】\n"
    "用户：{user_message}\n\n"
    "助手：{assistant_response}"
)


async def _build_review_agent():
    """构建专用的审查 Agent — 无人格、无记忆、受限工具集。"""
    from pydantic_ai import Agent
    from pydantic_ai.capabilities.reinject_system_prompt import ReinjectSystemPrompt

    from core.agent import LumenDeps, ProcessHistory, _clean_orphaned_tool_parts, _lumen_agent

    model = _lumen_agent._create_model()

    # 审查 Agent 只需要 memory + focus_update 工具
    from lib.tools._registry import get_tool_registry
    from lib.tools.factory import build_pydantic_toolset

    registry = get_tool_registry()
    review_tool_names = ["memory", "focus_update"]
    tools = [registry.get_tool(n) for n in review_tool_names]
    tools = [t for t in tools if t is not None]
    fixed_toolset = build_pydantic_toolset(tools)

    agent = Agent(
        model=model,
        deps_type=LumenDeps,
        output_type=str,
        system_prompt="你是记忆审查员。严格按照规则判断是否需要保存用户信息。",
        retries=1,
        end_strategy="graceful",
        capabilities=[
            ReinjectSystemPrompt(),
            ProcessHistory(_clean_orphaned_tool_parts),
        ],
    )

    @agent.toolset
    async def _review_toolset(ctx):
        return fixed_toolset

    return agent


async def background_memory_review(
    user_id: str,
    user_message: str,
    assistant_response: str,
    conversation_id: str,
) -> None:
    """后台审查本轮对话，判断是否有值得保存的记忆。

    使用独立的审查 Agent（无人格、无记忆快照、受限工具集），
    确保审查是客观的事实记录而非情感化的过度解读。
    """
    try:
        from core.agent import LumenDeps
        from core.db import get_async_session_maker
        from lib.memory.markdown import AsyncMarkdownStore

        agent = await _build_review_agent()

        # 读取当前记忆内容，供审查 Agent 做矛盾检测
        store = AsyncMarkdownStore()
        current_memory = await store.read_memory(user_id)
        if not current_memory.strip():
            current_memory = "（无现有记忆）"

        async with get_async_session_maker()() as db:
            deps = LumenDeps(
                user_id=user_id,
                db=db,
                conversation_id=conversation_id,
                current_user_input=user_message,
            )

            prompt = _REVIEW_SYSTEM_PROMPT.format(
                current_memory=current_memory[:2000],  # 截断防止 token 过长
                user_message=user_message,
                assistant_response=assistant_response,
            )

            await agent.run(prompt, deps=deps)
            await db.commit()

            logger.info(
                "后台记忆审查完成",
                conversation_id=conversation_id,
            )
    except Exception:
        logger.exception("后台记忆审查失败", conversation_id=conversation_id)
