"""Agent 编排引擎 — LangGraph 意图分类 + 流式生成"""

from __future__ import annotations

from pathlib import Path
from typing import TypedDict, Literal
from enum import StrEnum

from langgraph.graph import StateGraph, START, END
from langgraph.types import Command

from app.backend.agent.llm_router import get_model, TaskType, _get_client


# ═══════════════════════════════════════════════
# 意图枚举
# ═══════════════════════════════════════════════

class Intent(StrEnum):
    CONSULTATION = "consultation"
    PATH_PLANNING = "path_planning"
    RESUME = "resume"
    INTERVIEW = "interview"
    ANALYSIS = "analysis"
    TECHNICAL_QA = "technical_qa"
    EMOTIONAL = "emotional"


INTENT_TO_TASK: dict[str, TaskType] = {
    "consultation": "career_planning",
    "path_planning": "path_generation",
    "resume": "resume_optimize",
    "interview": "mock_interview",
    "analysis": "skill_analysis",
    "technical_qa": "general_chat",
    "emotional": "general_chat",
}


# ═══════════════════════════════════════════════
# LangGraph 意图分类（只做分类这一件事）
# ═══════════════════════════════════════════════

class ClassifyState(TypedDict):
    user_input: str
    intent: str


async def classify_intent_node(state: ClassifyState) -> Command[Literal["__end__"]]:
    """LLM 意图分类 → 结束图，返回 intent"""
    client = _get_client()

    prompt = f"""你是一个意图分类器。根据用户输入，从以下意图中选一个，只输出意图名称，不要解释。

意图类型：
- consultation：职业方向咨询（后端/前端/算法等方向的区别、前景、适合什么）
- path_planning：学习路径规划（怎么学、路线、多久能学会）
- resume：简历相关（优化简历、项目包装、投递策略）
- interview：面试准备（模拟面试、八股文、算法题）
- analysis：能力差距分析（和岗位差多少、哪里不足）
- technical_qa：纯技术问答（原理、为什么、怎么实现）
- emotional：情绪疏导（焦虑、迷茫、压力大）

用户输入：{state["user_input"]}
输出（只输出一个词）："""

    response = await client.chat.completions.create(
        model=get_model("general_chat"),
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        max_tokens=20,
    )
    intent_str = (
        response.choices[0].message.content.strip().lower()
        if response.choices
        else "consultation"
    )
    return Command(update={"intent": intent_str.replace("-", "_")}, goto=END)


def _build_classify_graph() -> StateGraph:
    workflow = StateGraph(ClassifyState)
    workflow.add_node("classify_intent", classify_intent_node)
    workflow.add_edge(START, "classify_intent")
    return workflow.compile()


_classify_graph = None


def get_classify_graph():
    global _classify_graph
    if _classify_graph is None:
        _classify_graph = _build_classify_graph()
    return _classify_graph


async def classify(user_input: str) -> tuple[str, TaskType]:
    """便捷函数：输入文本 → 返回 (intent, task_type)"""
    graph = get_classify_graph()
    result = await graph.ainvoke({"user_input": user_input, "intent": ""})
    intent = result.get("intent", "consultation")
    task = INTENT_TO_TASK.get(intent, "general_chat")
    return intent, task


# ═══════════════════════════════════════════════
# 系统提示词 — 按需从 SKILL.md 加载正文，仅分类 prompt 常驻
# ═══════════════════════════════════════════════

# 意图分类用（简短，~50 tokens）
_INTENT_PROMPTS: dict[str, str] = {
    "consultation": "当前任务：职业方向咨询。帮助用户了解计算机行业各方向，介绍工作内容、技能要求、发展前景。",
    "path_planning": "当前任务：学习路径规划。根据用户目标岗位、当前基础，生成结构化学习路径，标注学习资源和验收标准。",
    "resume": "当前任务：简历优化。基于 STAR 法则改进项目描述，提高 JD 匹配度，提供具体修改建议。",
    "interview": "当前任务：模拟面试。作为技术面试官，出题、追问、评分。覆盖八股/算法/系统设计/行为面试。",
    "analysis": "当前任务：能力差距分析。分析用户技能与目标岗位差距，标注等级，给出补全建议和时间估算。",
    "technical_qa": "当前任务：技术答疑。用大白话讲清楚技术原理和实现方式。",
    "emotional": "当前任务：情感疏导。接纳情绪，用数据和案例安抚，给出建设性建议。",
}

# skill 正文缓存（按需懒加载，首次命中才读文件）
_skill_body_cache: dict[str, str] = {}


def _load_skill_body(intent: str) -> str:
    """按需加载 SKILL.md 正文，缓存避免重复 IO"""
    if intent in _skill_body_cache:
        return _skill_body_cache[intent]

    dir_name = intent
    path = Path(__file__).parent / "skills" / dir_name / "SKILL.md"
    if not path.exists():
        return ""

    content = path.read_text(encoding="utf-8")
    body = content.split("---", 2)[-1].strip() if "---" in content else content.strip()
    _skill_body_cache[intent] = body
    return body


def build_system_prompt(user_profile: dict | None, intent: str) -> str:
    """组装系统提示词 — 分类 prompt 常驻 + Skill 正文按需加载"""
    parts = [
        "你是「码路领航」职业规划学长 Agent，一名研二计算机学长，在大厂实习过。",
        "风格：亲切、有干货、用大白话讲技术、不装腔作势。",
        "回答格式：【一句话总结】→ 详细解释 → 个性化建议 → 下一步行动。",
    ]
    if user_profile:
        p = user_profile
        parts.append(
            f"\n【用户背景】{p.get('nickname', '同学')}，"
            f"{p.get('grade', '')}，{p.get('school_name', '')}，"
            f"{p.get('major', '')}"
            f"\n目标方向：{p.get('target_direction', '未设定')}"
        )
        if p.get("current_skills"):
            skills = p["current_skills"]
            if isinstance(skills, list):
                names = [s.get("skill", "") for s in skills]
                parts.append(f"已掌握技能：{'、'.join(names)}")

    # Skill 正文：仅加载当前 intent 的 SKILL.md，不是全部 7 个
    body = _load_skill_body(intent)
    if body:
        parts.append(f"\n{body}")
    else:
        extra = _INTENT_PROMPTS.get(intent, "")
        if extra:
            parts.append(f"\n{extra}")
    return "\n".join(parts)
