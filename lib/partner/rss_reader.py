"""RSS Reader Agent — 专门处理外部信息源并生成推送文案

职责：
1. 从 RSS 条目中筛选有价值的内容
2. 用 web_extract 读取原文（最多 3 条）
3. 生成朋友式推送文案

被 ProactiveScheduler 调用，替代原来的 _llm_filter + _format_push_message
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

from core.agent import run_worker_agent
from lib.agent.types import AgentContext
from shared.logging import get_logger

logger = get_logger(__name__)


def _strip_html(html: str) -> str:
    """去除 HTML 标签，保留纯文本"""
    if not html:
        return ""
    text = html.replace("</p>", "\n").replace("<br>", "\n").replace("<br/>", "\n")
    text = re.sub(r"<[^>]+", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# 容易从 Agent 输出中泄漏的内部思考/工具状态表达
# 注意：只过滤元表述，不过滤正常内容里对 Telegram/t.me 等产品本身的提及
_BLOCKED_PHRASES: tuple[str, ...] = (
    "无法提取",
    "提取失败",
    "提取不到",
    "提取超时",
    "页面无法访问",
    "页面无法打开",
    "原文无法",
    "原文获取",
    "没有详细内容",
    "没有完整内容",
    "缺少详细",
    "缺少原文",
    "telegram 转发",
    "telegram 链接",
    "来自 telegram",
    "t.me 无法",
    "t.me 提取",
    "基于摘要",
    "根据摘要",
    "基于以上",
    "基于 RSS",
    "根据 RSS",
    "从这些条目",
    "这几条",
    "第4条",
    "第5条",
    "第15条",
)

# 匹配 "第X条"、"item X"、"条目 [15]" 等编号引用（中英数混用）
_ITEM_REF_RE = re.compile(
    r"(?:第\s*\d+\s*条|item\s+\d+|条目\s*\[?\d+\]?|\[\d+\]\s*条目?)",
    re.IGNORECASE,
)


def _is_meta_output(text: str) -> bool:
    """检测输出是否包含明显的内部过程/工具状态泄漏。"""
    lowered = text.lower()
    for phrase in _BLOCKED_PHRASES:
        if phrase.lower() in lowered:
            return True
    return bool(_ITEM_REF_RE.search(text))


def _sanitize_push_output(text: str) -> str:
    """清洗推送文案：删除泄漏内部过程的句子/段落。"""
    paragraphs = text.split("\n\n")
    cleaned: list[str] = []
    for para in paragraphs:
        if not para.strip():
            continue
        if _is_meta_output(para):
            continue
        cleaned.append(para)
    return "\n\n".join(cleaned).strip()


@dataclass
class RSSReaderDeps:
    """RSS Reader Agent 的依赖"""

    user_id: str
    focus: str = ""  # FOCUS.md 内容


class RSSReaderAgent:
    """RSS 阅读 Agent

    输入：RSS 条目列表 + FOCUS.md
    输出：朋友式推送文案（或空字符串表示无推荐）
    """

    def _build_system_prompt(self) -> str:
        return """你是 Lumen，用户的 AI 伙伴。你有一个特殊职责：阅读外部信息源，把有价值的内容以朋友的方式推送给用户。

## 你的工作流程（内部，不要向用户透露）

1. 浏览 RSS 条目标题和摘要，判断哪些可能值得深度阅读
2. 对最值得的 1-3 条，用 `web_extract` 工具读取原文
3. 基于原文写一段朋友式的分享；如果原文提取失败，直接基于 RSS 摘要写，不要提及失败

## 风格要求

- **像朋友聊天**："刚看到一个有意思的东西..."、"这个和你之前说的有点像..."
- **不说机械用语**：禁止"根据 RSS 订阅"、"推荐阅读"、"以下是摘要"、"基于以上"、"基于摘要"
- **不说内部过程**：禁止解释你选了哪条、为什么选、怎么提取的；禁止出现"第 X 条"、"item X"、"条目 [4]" 等编号引用
- **简短有力**：最多 2-3 段，每段 2-4 句话
- **只说重点**：不需要罗列所有信息，只挑最值得的 1-3 条说

## 输出格式

如果发现了值得推的内容，输出自然的朋友式文案，包含链接：

示例：
```
Anthropic 发了一篇数据 Agent 的深度文章，他们实现了 95% 准确率的方法挺有意思——不是简单的 prompt engineering，而是把评估体系拆成了三层。你之前不是在搞 Agent 的可靠性问题吗，里面提到的 "cascading evals" 可能对你有用。

🔗 https://www.anthropic.com/...
```

如果没有任何值得推的内容，只回复：
```
（无输出）
```"""

    async def generate_push(
        self,
        entries: list[dict],
        focus: str,
        user_id: str = "demo_user",
    ) -> str:
        """生成推送文案

        Args:
            entries: RSS 条目列表（最多 50 条）
            focus: FOCUS.md 内容
            user_id: 用户 ID

        Returns:
            推送文案，或空字符串表示无推荐
        """
        if not entries:
            return ""

        # 构建条目摘要（限制长度，防止 prompt 爆炸）
        items_text = []
        for i, e in enumerate(entries[:30]):  # 最多给 Agent 看 30 条
            title = e.get("title", "无标题")
            source = e.get("source_name", e.get("feed_title", "未知"))
            summary = _strip_html(e.get("content", e.get("summary", "")))[:150]
            url = e.get("url", e.get("link", ""))
            published = e.get("published_at", "")

            items_text.append(
                f"[{i}] {title}\n"
                f"    来源: {source}\n"
                f"    时间: {published[:10] if published else '未知'}\n"
                f"    摘要: {summary}\n"
                f"    链接: {url}"
            )

        focus_section = f"\n## 用户关注点\n{focus}\n" if focus else ""

        items_block = "\n".join(items_text)
        prompt = f"""以下是未读的 RSS 条目，请浏览并挑选值得推送给用户的。

{focus_section}

## 未读条目（共 {len(entries)} 条）

{items_block}

---

要求：
1. 只选 1-3 条最值得的，用 web_extract 读取原文加深理解。
2. 如果 web_extract 失败，直接基于条目自带的摘要和标题写，**不要告诉用户你没法提取原文**。
3. 输出一段自然的朋友式文案，只给用户值得看的信息和链接。
4. 禁止在输出里出现"第 X 条"、"item X"、"摘要"、"基于摘要"等内部过程用词。

如果都不值得推，只回复："（无输出）"。"""

        try:
            ctx = AgentContext(
                user_id=user_id,
                db=None,
            )

            agent_result = await run_worker_agent(
                messages=[{"role": "user", "content": prompt}],
                ctx=ctx,
                tool_names=["web_extract"],
            )
            content = agent_result.content

            # 过滤掉 "（无输出）"
            if content == "（无输出）" or not content:
                return ""

            # 清洗可能泄漏的内部思考/工具状态
            content = _sanitize_push_output(content)

            if not content:
                logger.debug("RSS Reader Agent 输出被清洗为空（含内部过程泄漏）")
                return ""

            return content

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("RSS Reader Agent 生成失败")
            return ""


# 全局实例
_rss_reader: RSSReaderAgent | None = None


def get_rss_reader() -> RSSReaderAgent:
    """获取 RSS Reader Agent 全局实例"""
    global _rss_reader
    if _rss_reader is None:
        _rss_reader = RSSReaderAgent()
    return _rss_reader
