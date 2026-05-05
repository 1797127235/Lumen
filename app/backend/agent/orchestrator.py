"""
Agent 编排引擎 — Skill 自发现 + 意图分类 + Prompt 组装

职责概览：
- 从 agent/skills/<intent>/SKILL.md 扫描并缓存技能元数据（intent 与目录名一致）。
- 用一次轻量 LLM 调用将用户输入分类到某个 intent，并得到该技能在 llm_router 中的 task_type。
- 将人设、可选用户画像、可选多轮摘要、当前技能正文拼成最终 system prompt。

注意：本模块不负责流式输出；上层拿到 (intent, task_type, system_prompt) 后再调用 llm_router。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import cast

import yaml

from app.backend.agent.llm_router import TaskType
from app.backend.agent.llm_router import chat as llm_chat

logger = logging.getLogger(__name__)

# 与本文件同级的 skills 目录；每个子目录名 = 意图标识 intent，内含 SKILL.md
_SKILLS_DIR = Path(__file__).parent / "skills"


@dataclass(frozen=True, slots=True)
class SkillMeta:
    """单个 Skill 的解析结果，供分类器列举选项与拼 system prompt 使用。"""

    intent: str  # 与 skills 子目录名一致，分类器应输出该英文标识
    name: str  # 展示用名称（来自 frontmatter，缺省为 intent）
    description: str  # 短描述：既给分类器看，也可在缺 body 时写入 prompt
    task_type: str  # 映射到 llm_router.TaskType，决定用哪档模型
    body: str  # SKILL.md 正文（去掉 frontmatter），通常含该场景下的详细指令


# 进程内单例缓存：避免每次请求都扫盘、读文件；新增/修改 SKILL.md 需重启进程生效
_skills_cache: dict[str, SkillMeta] | None = None


def discover_skills() -> dict[str, SkillMeta]:
    """
    扫描 skills/ 目录，解析每个子目录下的 SKILL.md。

    - YAML frontmatter：name / description / task_type 等（见各 SKILL.md）。
    - 正文：拼进 system prompt 的 skill 指令。

    返回 intent（目录名）→ SkillMeta 的映射；结果会被模块级缓存。
    """
    global _skills_cache
    if _skills_cache is not None:
        return _skills_cache

    skills: dict[str, SkillMeta] = {}
    if not _SKILLS_DIR.exists():
        _skills_cache = skills
        return skills

    # sorted：稳定顺序，便于日志与分类 prompt 中意图列表顺序一致
    for subdir in sorted(_SKILLS_DIR.iterdir()):
        if not subdir.is_dir():
            continue
        md_path = subdir / "SKILL.md"
        if not md_path.exists():
            continue

        try:
            content = md_path.read_text(encoding="utf-8")
            meta, body = _parse_skill_md(content)
        except Exception as e:
            logger.warning("解析 SKILL.md 失败: %s — %s", md_path, e)
            continue

        intent = subdir.name
        skills[intent] = SkillMeta(
            intent=intent,
            name=meta.get("name", intent),
            description=meta.get("description", ""),
            # 未写 task_type 时与通用闲聊同档，避免路由到未知类型
            task_type=meta.get("task_type", "general_chat"),
            body=body,
        )

    _skills_cache = skills
    return skills


def _parse_skill_md(text: str) -> tuple[dict, str]:
    """
    从 SKILL.md 中分离 YAML frontmatter 与 Markdown 正文。

    约定：以 --- 开头；第二个 --- 之后为正文。格式不合法时退化为「无 meta + 全文当正文」。
    """
    if not text.startswith("---"):
        return {}, text.strip()

    # 最多拆成 3 段：前缀空、frontmatter、正文，避免正文里出现的 --- 被误切
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text.strip()

    try:
        meta = yaml.safe_load(parts[1]) or {}
    except Exception:
        meta = {}

    body = parts[2].strip()
    return meta, body


def _sanitize_summary(text: str) -> str:
    """
    清洗对话摘要中可能夹带的「文件名 / 附件标记」。

    背景：摘要若来自含简历解析、上传文件的流水线，可能带 .pdf 等字样；
    不清洗时模型容易误以为要在对话里读取本地文件。
    """
    # 去掉方括号或中文括号包裹的、带常见文档扩展名的片段
    text = re.sub(r"[\[【].*?\.(?:pdf|docx?|png|jpg|txt)[\]】]", "", text, flags=re.IGNORECASE)
    # 去掉 "[PDF N]" 类页码/引用标记
    text = re.sub(r"\[PDF\s*\d+\]", "", text, flags=re.IGNORECASE)
    return text


# ── 意图分类 ──


def _validate_task_type(raw: str) -> TaskType:
    """
    将 SKILL 里配置的 task_type 约束为 llm_router 认识的 TaskType。
    TypedAlias 在运行时可枚举合法字面量；非法字符串统一回退 general_chat，防止路由崩溃。
    """
    valid = TaskType.__args__  # type: ignore[attr-defined]
    return cast(TaskType, raw if raw in valid else "general_chat")


async def classify(user_input: str) -> tuple[str, TaskType]:
    """
    根据用户输入选择 intent，并返回该 skill 对应的 task_type。

    流程：LLM 分类 → 查表 → 必要时模糊匹配 → 再必要时默认 consultation 或任意一项。
    """
    skills = discover_skills()
    if not skills:
        # 没有任何 SKILL 文件时仍返回合法元组，便于上层继续走通用模型
        return "consultation", _validate_task_type("general_chat")

    intent = await _classify_llm(user_input, skills)
    skill = skills.get(intent)
    if skill is None:
        # 模型可能输出近似词、或带多余前后缀：用子串双向包含做一次宽松匹配
        for key in skills:
            if key in intent or intent in key:
                skill = skills[key]
                break
        if skill is None:
            # 优先落到 consultation；若目录里甚至没有该 intent，则取任意一个 skill 避免 None
            skill = skills.get("consultation") or next(iter(skills.values()))
    return skill.intent, _validate_task_type(skill.task_type)


async def _classify_llm(user_input: str, skills: dict[str, SkillMeta]) -> str:
    """
    单次 LLM 调用：只输出一个 intent 字符串（英文、与目录名一致）。

    使用 general_chat 档位模型即可：分类任务轻量，且与路由里「闲聊/轻推理」一致。
    """
    lines = [
        "你是一个意图分类器。根据用户输入，从以下意图中选一个，只输出意图名称（英文），不要解释。\n",
        "意图类型：",
    ]
    for intent, skill in skills.items():
        lines.append(f"- {intent}：{skill.description}")
    lines.append(f"\n用户输入：{user_input}")
    lines.append("输出（只输出一个词）：")

    content = await llm_chat(
        task_type="general_chat",
        messages=[{"role": "user", "content": "\n".join(lines)}],
        temperature=0.0,
        max_tokens=20,
    )

    if not content:
        return "consultation"
    # 与文件夹命名惯例对齐：小写 + 下划线（如 path-planning → path_planning）
    return content.strip().lower().replace("-", "_")


async def _retrieve_memories(user_input: str, user_id: str) -> list[str]:
    """语义检索 Cognee 中与 user_input 相关的历史记忆，失败返回空列表。

    注意：PydanticAI 版本中，此函数由 @agent.system_prompt 装饰器替代。
    保留此函数用于向后兼容。
    """
    from app.backend.services import cognee_service

    try:
        # 使用 Cognee recall 检索
        results = await cognee_service.recall(user_id, user_input, limit=5)
        return [r.strip() for r in results if r.strip()]

    except Exception:
        logger.warning("Cognee 检索失败，回退至无记忆上下文: user_id=%s", user_id)
        return []


# ── Prompt 组装 ──
def build_system_prompt(
    user_profile: dict | None,
    intent: str,
    conversation_summary: str | None = None,
    memories: list[str] | None = None,
) -> str:
    """
    拼接发给模型的 system 消息内容。

    顺序：全局人设与格式 → 用户画像（可选）→ 对话摘要（可选）→ 当前 intent 的 SKILL 正文或描述。
    """
    skills = discover_skills()
    skill = skills.get(intent)

    # 与产品设定一致的底色；日期用于时效性问题（校招季、技术趋势等）
    parts = [
        "你是「码路领航」职业规划学长 Agent，一名研二计算机学长，在大厂实习过。",
        f"今天是 {date.today().isoformat()}。涉及市场行情、薪资、技术栈热度时，请基于这个时间点判断。",
        "风格：亲切、有干货、用大白话讲技术、不装腔作势。",
        "回答格式：【一句话总结】→ 详细解释 → 个性化建议 → 下一步行动。",
    ]

    if user_profile:
        p = user_profile
        nickname = p.get("nickname") or "同学"
        grade = p.get("grade") or ""
        school = p.get("school_name") or ""
        major = p.get("major") or ""
        target = p.get("target_direction") or "未设定"

        bg_parts = []
        if grade:
            bg_parts.append(grade)
        if school:
            bg_parts.append(school)
        if major:
            bg_parts.append(major)

        bg_line = f"【用户背景】{nickname}"
        if bg_parts:
            bg_line += f"，{'、'.join(bg_parts)}"
        bg_line += f"\n目标方向：{target}"
        parts.append(bg_line)

        # current_skills 在 ORM 里可能是 list[dict]，每项含 skill 名字段
        skills_data = p.get("current_skills")
        if skills_data and isinstance(skills_data, list):
            names = [s.get("skill", "") for s in skills_data if isinstance(s, dict) and s.get("skill")]
            if names:
                parts.append(f"已掌握技能：{'、'.join(names)}")

        # 防止模型在「泛问行业/八股」时仍强行引用简历字段
        parts.append(
            "以上是用户画像。如果用户当前问题与画像内容无关（例如换了话题、"
            "问的是行业趋势而非个人情况），不要强行关联画像，直接回答问题。"
        )

    # ── Mem0 历史记忆注入 ──────────────────────────
    if memories:
        logger.debug("注入 Mem0 记忆 %d 条: %s", len(memories), memories)
        mem_lines = "\n".join(f"- {m}" for m in memories)
        parts.append(f"【相关历史记忆】\n{mem_lines}")
    # ─────────────────────────────────────────────────

    if conversation_summary:
        # 控制长度，降低 token；并清洗文件名等噪声
        summary = _sanitize_summary(conversation_summary[:500])
        parts.append(f"【对话摘要】{summary}")

    # 优先注入完整 SKILL 正文；仅有 description 时退化为一句任务说明
    if skill and skill.body:
        parts.append(f"\n{skill.body}")
    elif skill and skill.description:
        parts.append(f"\n当前任务：{skill.description}")

    return "\n".join(parts)


# ── 统一入口 ──
async def run_orchestrator(
    user_input: str,
    user_profile: dict | None,
    conversation_summary: str | None = None,
    user_id: str = "demo_user",
) -> tuple[str, TaskType, str]:
    """
    编排单次对话所需的静态上下文（不含用户本轮 user 消息）。

    注意：PydanticAI 版本中，系统提示词由 @agent.system_prompt 装饰器动态生成。
    此函数保留用于向后兼容和意图分类。

    参数:
        user_input: 用户本轮输入，用于意图分类。
        user_profile: 画像字典，结构与 profile 接口一致；可为 None。
        conversation_summary: 可选的多轮摘要，通常由上层从 DB 或 LLM 维护。
        user_id: 用户 ID，用于记忆检索过滤。

    返回:
        intent: 选中的技能目录名。
        task_type: 供 llm_router 选择模型与参数。
        system_prompt: 完整 system 字符串，与 user 消息一起发给聊天接口。
    """
    intent, task_type = await classify(user_input)

    # 检索相关历史记忆（PydanticAI 版本中由 @agent.system_prompt 处理）
    memories = await _retrieve_memories(user_input, user_id)

    system = build_system_prompt(user_profile, intent, conversation_summary, memories=memories)
    return intent, task_type, system
