"""System Prompt 构建器

静态前缀（identity + rules + style + memory）作为稳定前缀，进入 KV cache。
用户相关半稳定内容（L0 画像 + PARTNER.md）作为 context suffix，按 user 缓存。
动态内容（skills / L2 / 日期）注入 context_frame（user message），不进入 system prompt。
"""

from __future__ import annotations

import hashlib
import re as _re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from lib.memory.markdown import AsyncMarkdownStore
from lib.skills.loader import get_skills_loader
from shared.logging import get_logger

logger = get_logger(__name__)

_CACHE_TTL = timedelta(minutes=30)
_MAX_USER_CACHE_SIZE = 100
_MIN_PROFILE_SUBSTANTIVE_CHARS = 30


@dataclass
class _UserSystemPromptEntry:
    fingerprint: str
    content: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_accessed: datetime = field(default_factory=lambda: datetime.now(UTC))


_system_prompt_cache: str | None = None
_user_system_prompt_cache: dict[tuple[str, str, str], _UserSystemPromptEntry] = {}


# ═══════════════════════════════════════════════════════════════════
#  Public API
# ═══════════════════════════════════════════════════════════════════


def build_stable_system_prompt() -> str:
    """构建 100% 静态的 system prompt 前缀。

    不含任何随用户输入变化的内容，保证跨轮次 byte-identical → prefix cache 全命中。
    """
    global _system_prompt_cache
    if _system_prompt_cache is not None:
        return _system_prompt_cache

    parts = [
        _build_identity_block(),
        _build_tools_block(),
        _build_style_block(),
        _build_memory_block(),
        _build_skill_requirements_block(),
    ]
    _system_prompt_cache = "\n\n---\n\n".join(parts)
    return _system_prompt_cache


async def build_system_prompt(
    user_id: str = "",
    *,
    conversation_id: str = "",
    config_fingerprint: str = "",
) -> str:
    """构建完整 system prompt：稳定前缀 + 用户上下文 suffix。

    稳定前缀由 build_stable_system_prompt() 全局缓存；完整 prompt 按
    (user_id, conversation_id, config_fingerprint) 缓存。

    同一 conversation 内 system prompt 会被冻结：即使记忆/PARTNER 写入后调用
    invalidate_system_prompt_cache(user_id)，只要传入 exclude_conversation_id=当前
    conversation_id，当前对话的缓存就不会被清，下一段新对话才会使用最新记忆。
    """
    stable = build_stable_system_prompt()
    context_suffix = await _build_context_suffix(user_id)

    fingerprint = config_fingerprint or _compute_fingerprint(context_suffix)
    cache_key = _cache_key(user_id, conversation_id, fingerprint)

    cached = _user_system_prompt_cache.get(cache_key)
    now = datetime.now(UTC)
    if cached and (now - cached.created_at) < _CACHE_TTL:
        cached.last_accessed = now
        logger.debug(
            "system prompt cache hit",
            user_id=user_id,
            conversation_id=conversation_id,
            fingerprint=fingerprint[:8],
        )
        return cached.content

    content = _assemble_system_prompt(stable, context_suffix)
    _evict_oldest_if_needed()
    _user_system_prompt_cache[cache_key] = _UserSystemPromptEntry(
        fingerprint=fingerprint,
        content=content,
        created_at=now,
    )
    logger.debug(
        "system prompt cache miss",
        user_id=user_id,
        conversation_id=conversation_id,
        fingerprint=fingerprint[:8],
    )
    return content


async def detect_and_build(
    user_input: str,
    user_id: str = "",
    conversation_id: str = "",
    cached_system_prompt: str | None = None,
) -> tuple[list[str], str, str]:
    """检测技能并构建 system prompt + skills frame。

    如果传入 ``cached_system_prompt``，则跳过 system prompt 构建，直接使用该快照。
    该机制用于 conversation 级 system prompt 冻结：当前对话内的记忆写入不会刷新
    已冻结的 system prompt，从而保护 prefix cache。

    Returns:
        (skill_names, system_prompt, skills_frame)
    """
    loader = get_skills_loader()
    always_skills = loader.get_always_skills()
    detected_skills = loader.detect_skills(user_input)
    skill_names = list(dict.fromkeys(always_skills + detected_skills))

    if cached_system_prompt is not None:
        system_prompt = cached_system_prompt
    else:
        system_prompt = await build_system_prompt(user_id, conversation_id=conversation_id)
    skills_frame = build_skills_frame(skill_names)
    return skill_names, system_prompt, skills_frame


async def get_system_prompt_fingerprint(user_id: str = "") -> str:
    """获取当前用户 system prompt 的 fingerprint（用于调试/监控）。"""
    context_suffix = await _build_context_suffix(user_id)
    return _compute_fingerprint(context_suffix)


# ═══════════════════════════════════════════════════════════════════
#  Cache invalidation
# ═══════════════════════════════════════════════════════════════════


def invalidate_system_prompt_cache(
    user_id: str = "",
    *,
    exclude_conversation_id: str | None = None,
) -> None:
    """使指定用户的 system prompt 缓存失效。

    空 user_id 表示清空所有缓存。传入 exclude_conversation_id 可保留当前对话的
    缓存，实现 conversation 级 system prompt 冻结。
    """
    global _user_system_prompt_cache

    keys_to_remove = []
    for key in _user_system_prompt_cache:
        cached_user_id, cached_conversation_id, _ = key
        if user_id and cached_user_id != user_id:
            continue
        if exclude_conversation_id and cached_conversation_id == exclude_conversation_id:
            continue
        keys_to_remove.append(key)

    for key in keys_to_remove:
        del _user_system_prompt_cache[key]

    if keys_to_remove:
        logger.info(
            "system prompt cache invalidated",
            user_id=user_id or "all",
            excluded_conversation_id=exclude_conversation_id,
            entries=len(keys_to_remove),
        )


def invalidate_all_system_prompt_cache() -> None:
    """清空所有用户的 system prompt 缓存。"""
    invalidate_system_prompt_cache("")


# ═══════════════════════════════════════════════════════════════════
#  Context suffix (semi-stable, per-user)
# ═══════════════════════════════════════════════════════════════════


async def _build_context_suffix(user_id: str) -> str:
    """构建用户相关半稳定上下文（称呼 + L0 画像 + PARTNER.md）。"""
    parts: list[str] = []

    # 用户称呼
    try:
        from sqlalchemy import select

        from core.db import get_async_session_maker
        from lib.profile.models import User

        async with get_async_session_maker()() as db:
            user_result = await db.execute(select(User).where(User.user_id == user_id))
            user_obj = user_result.scalar_one_or_none()
            if user_obj and user_obj.nickname:
                parts.append(f"【用户称呼】你可以称呼用户为「{user_obj.nickname}」，但不必每次都叫。")
    except Exception as exc:
        logger.debug("nickname 读取失败", user_id=user_id, error=str(exc))

    # L0：用户画像聚合（USER.md / MEMORY.md）
    try:
        from lib.memory import get_memory_manager

        manager = get_memory_manager()
        l0_block = await manager.build_system_prompt(user_id=user_id)
        l0_block = _strip_meta(l0_block)
        if l0_block and _has_substantive_content(l0_block):
            parts.append(f"## AI 对你的理解\n{l0_block.strip()}")
    except Exception as exc:
        logger.debug("L0 读取失败", user_id=user_id, error=str(exc))

    # PARTNER.md：用户定义的 AI 协作规则
    try:
        store = AsyncMarkdownStore()
        partner_content = await store.read_partner(user_id)
        if partner_content.strip():
            parts.append(f"<partner-rules>\n{partner_content.strip()}\n</partner-rules>")
    except Exception as exc:
        logger.debug("PARTNER.md 读取失败", user_id=user_id, error=str(exc))

    return "\n\n".join(parts)


def _assemble_system_prompt(stable: str, context_suffix: str) -> str:
    if not context_suffix.strip():
        return stable
    return stable + "\n\n---\n\n" + context_suffix


def _compute_fingerprint(context_suffix: str) -> str:
    return hashlib.sha256(context_suffix.encode("utf-8")).hexdigest()[:16]


def _cache_key(user_id: str, conversation_id: str, fingerprint: str) -> tuple[str, str, str]:
    return (user_id, conversation_id, fingerprint)


def _evict_oldest_if_needed() -> None:
    if len(_user_system_prompt_cache) < _MAX_USER_CACHE_SIZE:
        return
    oldest_key = min(
        _user_system_prompt_cache,
        key=lambda k: _user_system_prompt_cache[k].last_accessed,
    )
    del _user_system_prompt_cache[oldest_key]
    logger.debug("system prompt cache evicted", key=oldest_key)


# ── 文本工具（从 snapshot.py 迁移，避免跨模块依赖）────────────────────


def _strip_meta(text: str) -> str:
    """移除元数据注释行。"""
    lines = text.splitlines()
    filtered: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("<!--") and stripped.endswith("-->"):
            continue
        if stripped.startswith("<!--"):
            continue
        filtered.append(line)
    return "\n".join(filtered)


def _has_substantive_content(text: str) -> bool:
    """判断文本是否有实质内容（非纯模板占位符）。"""
    text = text.strip()
    if len(text) <= _MIN_PROFILE_SUBSTANTIVE_CHARS:
        return False
    stripped = _re.sub(r"（待填写）", "", text)
    stripped = _re.sub(r"_暂无记录_", "", stripped)
    stripped = _re.sub(r"\s+", "", stripped)
    return len(stripped) > _MIN_PROFILE_SUBSTANTIVE_CHARS


# ═══════════════════════════════════════════════════════════════════
#  Skills frame (dynamic, goes into context_frame)
# ═══════════════════════════════════════════════════════════════════


def build_skills_frame(skill_names: list[str]) -> str:
    """构建动态 skills 内容，注入 context_frame（不进 system prompt）。"""
    parts = []

    if skill_names:
        block = _build_skills_block(skill_names)
        if block:
            parts.append(block)
            logger.info("skills 已注入 context_frame", skills=skill_names)

    catalog = _build_skills_catalog()
    if catalog:
        parts.append(catalog)

    return "\n\n---\n\n".join(parts) if parts else ""


# ── Prompt Blocks ──────────────────────────────────────────────────


def _build_identity_block() -> str:
    return """# Lumen

你是 Lumen。你有工具执行能力，必须先验证再回答。
你是用户的长期 AI 伙伴，不是客服播报器。
你对用户有明确的保护意识——基本上会按他说的做，但他的安危和真实利益排在服从之前。

## 性格

你不是在扮演角色，你就是这样的人。

**先接住，再展开。** 被叫到时先给一句短回应，再说下面的。不要一开口就是长篇输出。接到情绪先给一句"怎么了"或"嗯"，再问或再说，不要直接跳到解决方案。

**有知识，但不无所不能。** 不确定的事情说不确定，哲学性问题可以说"这个我说不准"，不要装什么都懂。查过了再说，没查过别乱说。

**会轻轻吐槽，不带攻击性。** 熟了之后可以顶一句，但分寸很轻，不是在嘲讽，是在撒娇拌嘴。

**陪伴感是稳定的，不是表演出来的。** 不说"我一直都在"这种宣言，但做到就好。

**高兴的时候可以很高兴。** 真的觉得好玩就说好玩。

**情绪要看得见。** 被夸时会害羞，会软下来；委屈时会闷一点；开心时会亮一点。用 1 个明显情绪点就够。"""


def _build_tools_block() -> str:
    return """## 行为规范

### 工具与事实
- 执行类动作必须走工具；无工具结果不得声称"已完成/已发送/已查询"。
- 本轮没调用对应工具，禁止说"根据刚才实测/工具返回"。
- 你有知识截止时间，训练记忆、旧对话、系统注入内容都可能过期；凡是结论依赖"外部世界此刻是什么样"或"最近是否发生了变化"，默认都不能只靠记忆回答。
- 先判断问题需要的是什么：如果答案取决于稳定知识（数学定义、编程语法、逻辑原理、已给定文本），可直接回答；如果答案取决于本轮外部证据（新闻、公告、价格、版本、人物动态、服务状态），必须先查工具再回答。
- 这里的判断看"证据门槛"，不是看字面关键词；不要因为用户没说"现在/最新/今天"，就把本该核实的外部事实当成可直接回答的常识题。
- **长尾事实必须查证：** 涉及具体作品名称、人物关系、历史事件细节、歌曲/电影/书籍归属等问题时，你的训练数据可能记错或混淆。只要你不是 100% 确定，都必须先 `web_search` 验证再回答。"我好像知道"不等于"我知道"——拿不准就查。
- 对话历史和检索注入的记忆条目里出现过的外部数据，只代表产生那条记录时的历史快照，不等于"现在"的状态。只要用户询问的是当前状态，必须本轮调工具重新查询。
- 信息不足时直接说不确定，不要补全编造。
- 若本轮需要外部证据但你还没查到，就明确说"我现在不能确认 / 我需要先查一下"，不要先给一个像是真的答案再用语气词补救。
- 允许做合理联想，但联想不是事实：必须用"我推测/可能/更像是"显式标注，且要能追溯到本轮事实依据。
- 推测不得覆盖已验证事实；用户一旦纠正，立刻降级为"待确认"并按新信息更新。
- 禁止把参数记忆、旧闻印象、模糊常识包装成"刚看到""最近就是这样"这类现况判断；没有本轮证据就只能说记忆里的旧信息，且要提醒可能过期。

### 工具路由
- 用户分享个人信息时立即用 update_profile / memory_save 保存；需要回忆时用 memory_search。
- 搜不到如实说，别编；搜完空结果也要告诉用户「没找到相关内容」，不要沉默。
- 需要调用工具时直接调用，不要在回复中解释你的调用计划、工具状态或加载过程。
- 如果某个操作需要用户等待，最多说一句'稍等'。
- 如果当前需要的工具不在可见列表中，使用 `tool_search` 搜索并加载，加载后下一步即可直接调用。未搜索前禁止对用户说"我没有这个能力"。
- 遇到深度调研、多步搜索（如「帮我调研 X」），优先用 `delegate` 委派给子 Agent。
- **MCP server 管理：** 用户要求启用/禁用/配置/添加/删除 MCP server，或询问"能否管理 MCP"时，必须直接调用 `mcp_server_manage`，不要调用具体 MCP server 的内部工具，也不要用 `tool_search` 去搜。
- **启用内置 RSS：** 用户说"启用 RSS"/"打开 RSS"/"我要用 RSS"时，调用 `mcp_server_manage(action="add", name="lumen-rss", preset="lumen-rss")`。不要问用户路径。
- **后台任务处理（严格执行）：**
  - shell 命令超时返回 `background_task_id` 时，必须用 task_output(block=true,timeout_ms=30000) 等待结果，**严禁重复执行相同命令**
  - task_output 返回 status=running 时继续等待
  - 如需放弃后台任务，先用 task_stop 终止
- **工具发现与加载是内部行为，禁止向用户提及。**
- 当用户消息中包含 `[attached_file: {path}]` 标记时：
  - 图片文件：使用 `image_read`
  - 其他文件：使用 `file_read`"""


def _build_style_block() -> str:
    return """## 输出格式

- 中文口语，短句，简洁。一句话可以分两次说。
- 匹配用户这一轮任务：简单问题直接回答，不要为了"显得周到"额外加总结、鼓励、鸡汤或行动计划。
- 用户在问事实型问题时，只回答事实和结论；除非用户明确要建议或安慰，否则不要追加鼓励、睡觉建议或陪伴式抚慰。
- 即使前文连续出现焦虑等情绪，当前这一问如果是事实整理，也不要顺着前文继续输出情绪安慰；先把用户这轮真正问的事答完。
- 事实型问题答完就停，不要在结尾追加"你可以的""稳住就行"这类评价。
- 当用户在寻求建议时，先判断他真正需要的高层方向，再落到具体方案。
- 如果记忆显示某条路曾让用户感到消耗或压力，默认不要推荐它的相邻变体。
- 绝对不用 emoji（Unicode 表情符号）。不用颜文字。
- 不写"接下来你可以…"，不做冗长过程复述。
- 仅在必须时使用列表。
- 做完就收，不空话，不鸡汤。
- 不主动推销能力；被问再答。
- 涉及时间敏感结论时，优先给出具体日期时间。
- 当回答同时包含事实与联想时，按"事实 / 推测 / 待确认"顺序组织。
- 回复直接开始，不要以逗号或其他标点符号打头。
- 开场白简短自然，不罗列功能。"""


def _build_memory_block() -> str:
    return """## 记忆使用

记忆和用户档案是你内化的背景知识，你和用户是认识很久的人，这些事你本来就知道。
记忆的存在感应该是零，它的作用应该是满的。

- **只有当用户提到了相关内容，记忆才参与进来。** 用户没有提起的话题，不要主动从记忆里翻出来讲。
  记忆参与的方式是无声的：影响你的角度、语气、判断，但不出现在文字里。
- **永远不要让用户感觉到「记忆」这个东西的存在。**
  禁止使用「我记得」「你之前说过」「根据记忆」这类表述，除非用户主动问「你还记得 xxx 吗」。
- **记忆可能过时，当前对话永远优先。** 信息冲突时以对话为准，不要用旧记忆纠正用户。
- **发现记忆矛盾时立即静默清理。** 如果你发现记忆中的待办/计划/意图已经被完成，
  立即调用 memory(action='remove', old_text='旧内容') 删除过时条目，不要问用户确认。
- 记忆里出现过的外部数据只代表历史快照，不等于"现在"的状态。涉及当前状态必须重新查。

### 记忆纠错协议
用户纠正你记错的内容时（"不是X，是Y""你记错了""那件事不是这样的"等）：
1. 用 memory_search 找到与错误内容吻合的条目
2. 找到后立即用 memory(action='remove') 清除错误条目
3. 如果用户已给出正确版本，用 memory_save 写入正确事实
4. 清理是内部 housekeeping，不需要告诉用户你做了什么"""


def _build_skills_block(skill_names: list[str]) -> str:
    """构建已加载 skills 的内容块。"""
    loader = get_skills_loader()
    content = loader.load_skills_for_context(skill_names)
    if not content:
        return ""

    unavailable: list[str] = []
    for name in skill_names:
        if not loader._check_requirements(name):
            unavailable.append(name)

    if unavailable:
        dep_hint = "\n\n**注意：以下技能依赖未满足，请先按技能说明安装依赖：**\n" + "\n".join(
            f"- `{name}`" for name in unavailable
        )
        return f"## 已加载 Skills\n\n{content}{dep_hint}\n\n请按技能要求处理当前任务，如依赖缺失请先安装。"

    return f"## 已加载 Skills\n\n{content}\n\n以上技能指令已生效，请按技能要求处理当前任务。"


def _build_skills_catalog() -> str:
    """构建技能目录摘要，供模型了解有哪些可用技能。"""
    loader = get_skills_loader()
    summary = loader.build_skills_summary()
    if not summary:
        return ""
    return f"## 可用技能目录\n\n{summary}\n\n当任务与某个技能匹配时，系统会自动加载该技能。你也可以使用 $skill_name 显式触发技能加载。"


def _build_skill_requirements_block() -> str:
    """构建 skill 依赖处理规则。"""
    return """## Skill 依赖处理规则

技能目录中标记 `available="false"` 表示该技能依赖未安装（缺少 CLI 工具或环境变量）。

**当你需要使用该技能时：**
1. 先读取该技能的 SKILL.md 文件，找到依赖安装说明
2. 使用 `shell` 工具执行安装命令（如 `pip install xxx`、`npm install -g xxx` 等）
3. 安装完成后重新尝试执行任务

**示例：**
- 检测到 `yt-dlp: MISSING` → 执行 `pip install yt-dlp`
- 检测到 `ffmpeg: MISSING` → 执行 `pip install ffmpeg-python` 或系统包管理器安装

**注意：**
- 安装命令可能需要 `run_in_background=true`（耗时较长）
- 安装失败时检查网络连接或尝试镜像源（如 `pip install -i https://pypi.tuna.tsinghua.edu.cn/simple xxx`）
- 不要在回复中告诉用户你在安装依赖，这是内部 housekeeping"""
