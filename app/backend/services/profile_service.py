"""画像服务 — 简历文本提取 + LLM 解析 + 写入 .md 文件"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from fastapi import HTTPException, UploadFile

from app.backend.agent.llm_router import chat as llm_chat

logger = logging.getLogger(__name__)

# ── 文件限制 ──
_MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB

# ── LLM 参数 ──
_LLM_TEMPERATURE = 0.1
_LLM_MAX_TOKENS = 4096
_LLM_TRUNCATION_LENGTH = 5000  # 截断过长文本（前 N 字符信息密度最高）
_MAX_RETRIES = 3  # JSON 截断时重试次数

# ── 预览 ──
_PREVIEW_LENGTH = 500  # 简历文本预览长度

# ── 默认值 ──
_DEFAULT_SKILL_LEVEL = "familiar"

# 允许上传的简历文件扩展名白名单
_ALLOWED_SUFFIXES = {".pdf", ".docx", ".doc", ".txt", ".md", ".html", ".htm"}


async def _extract_text(file: UploadFile) -> str:
    content = await file.read()
    if len(content) > _MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="文件超过 10MB 限制")

    filename = file.filename or "unknown"
    return await asyncio.to_thread(_extract_with_markitdown, content, filename)


def _extract_with_markitdown(content: bytes, filename: str) -> str:
    """使用 markitdown 将文件转为 Markdown 文本。"""
    import tempfile
    from pathlib import Path

    from markitdown import MarkItDown

    suffix = Path(filename).suffix.lower() or ".tmp"
    if suffix not in _ALLOWED_SUFFIXES:
        raise HTTPException(status_code=415, detail=f"不支持的文件类型: {suffix}")
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        md = MarkItDown()
        result = md.convert(str(tmp_path))
        text = result.text_content
        if not text or not text.strip():
            raise ValueError("markitdown 返回空内容")
        return text
    finally:
        tmp_path.unlink(missing_ok=True)


# ═══════════════════════════════════════════════
# LLM 解析 → Markdown
# ═══════════════════════════════════════════════

_PROFILE_EXTRACT_PROMPT = """从简历中提取信息，直接输出结构化的 Markdown 格式。

输出要求：
1. 严格按照下面的模板格式输出
2. 只输出 Markdown 内容，不要有任何其他文字
3. 如果某个字段简历中没有提到，填写"（待填写）"
4. 不要添加简历中没有的信息

输出模板：

# 用户核心记忆

> 这个文件由 AI 自动管理，记录用户的核心信息。
> 每次对话开始时会自动注入到 system prompt。

## 基础信息
- 学校：（从简历提取）
- 专业：（从简历提取）
- 年级：（从简历提取）
- 毕业年份：（从简历提取）
- 学校层次：（从简历提取）

## 目标方向
- 目标岗位：（从简历提取）
- 目标公司类型：（从简历提取）
- 意向城市：（从简历提取）

## 教育背景
- GPA：（从简历提取）
- 排名：（从简历提取）
- 获奖：（从简历提取）

## 当前状态
- 正在学习：（待填写）
- 正在准备：（待填写）
- 焦虑程度：（待填写）

## 个人简介
（从简历提取）

## 英语水平
（从简历提取）

## 期望薪资
（从简历提取）

---

技能部分请用以下格式（单独输出）：

# 技能列表

## 已掌握技能

### 技能名称
- 状态：了解/熟悉/精通/专家
- 备注：xxx

---

项目经历请用以下格式（单独输出）：

# 项目经历

## 项目经历

### 项目名称
- 时间：xxx
- 技术栈：xxx
- 角色：xxx
- 描述：xxx

---

工作经历请用以下格式（单独输出）：

# 工作经历

## 实习经历

### 公司名称 - 岗位
- 时间：xxx
- 描述：xxx

---

年级映射：
- 大一=freshman, 大二=sophomore, 大三=junior, 大四=senior
- 研一=graduate1, 研二=graduate2, 研三=graduate3

学校层次映射：
- 985/211/双一流/普通本科

目标方向可选：后端/前端/算法/AI/测试/运维/安全/客户端/数据/嵌入式/其他

目标公司类型可选：top(头部大厂)/major(一线大厂)/medium(中型企业)/state_owned(国企)

技能水平可选：beginner(了解)/familiar(熟悉)/intermediate(熟练)/advanced(精通)

简历内容：
{resume_text}"""


async def _llm_extract_to_markdown(raw_text: str) -> str:
    """调用 LLM 从简历文本提取信息，直接输出 Markdown 格式"""
    truncated = raw_text[:_LLM_TRUNCATION_LENGTH]
    prompt = _PROFILE_EXTRACT_PROMPT.format(
        current_year=datetime.now().year,
        resume_text=truncated,
    )

    result = await llm_chat(
        task_type="skill_analysis",
        messages=[
            {
                "role": "system",
                "content": "你是一个简历解析助手。从简历中提取信息，按照指定的 Markdown 格式输出。只输出 Markdown 内容，不要有任何其他文字。",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=_LLM_TEMPERATURE,
        max_tokens=_LLM_MAX_TOKENS,
    )

    # 清理输出，确保是 Markdown 格式
    result = result.strip()
    if not result.startswith("#"):
        # 如果 LLM 没有以 # 开头，尝试提取 Markdown 部分
        lines = result.split("\n")
        md_start = next((i for i, line in enumerate(lines) if line.startswith("#")), 0)
        result = "\n".join(lines[md_start:])

    return result


# ═══════════════════════════════════════════════
# 主流程：上传 → 提取 → 解析 → 保存
# ═══════════════════════════════════════════════


async def process_resume_to_memory(file: UploadFile, user_id: str = "demo_user") -> dict:
    """完整管线：上传简历 → 解析 → 写入 .md 文件

    Args:
        file: 上传的简历文件
        user_id: 用户 ID

    Returns:
        dict: 包含 success, message, preview 字段
    """
    filename = file.filename or "unknown"

    # 1. 文本提取
    try:
        raw_text = await _extract_text(file)
        logger.info("[1/3] 文本提取成功: %s, %d chars", filename, len(raw_text))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[1/3] 文本提取失败: %s", filename)
        raise HTTPException(status_code=422, detail=f"文件读取失败: {e}")

    # 2. LLM 解析 → Markdown
    try:
        markdown_content = await _llm_extract_to_markdown(raw_text)
        if not markdown_content or len(markdown_content) < 50:
            raise HTTPException(status_code=422, detail="LLM 未返回有效内容，请确认简历内容清晰可读")
        logger.info("[2/3] LLM 解析成功: %d chars", len(markdown_content))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[2/3] LLM 解析失败")
        raise HTTPException(status_code=502, detail=f"AI 解析失败: {e}")

    # 3. 写入 .md 文件
    try:
        # 导入 memory_service 的写入函数
        from app.backend.services.memory_service import write_memory

        # 直接写入 memory.md
        write_memory(markdown_content)
        logger.info("[3/3] 画像保存成功: memory.md")
    except Exception as e:
        logger.exception("[3/3] 画像保存失败")
        raise HTTPException(status_code=500, detail=f"数据保存失败: {e}")

    # 4. 写入成长事件（fire-and-forget）
    try:
        from app.backend.db.base import get_async_session_maker
        from app.backend.services.growth_event_service import create_growth_event

        async def _create_resume_events():
            async with get_async_session_maker()() as db:
                # 简历上传事件
                await create_growth_event(
                    db=db,
                    user_id=user_id,
                    event_type="resume_uploaded",
                    entity_type="profile",
                    payload={"filename": filename, "content_length": len(markdown_content)},
                    source="简历提取",
                    project=True,
                )
                # 画像更新事件
                await create_growth_event(
                    db=db,
                    user_id=user_id,
                    event_type="profile_updated",
                    entity_type="profile",
                    payload={"field": "resume", "source": "简历解析"},
                    source="简历提取",
                    project=True,
                )
                await db.commit()

        task = asyncio.create_task(_create_resume_events())
        # 存储任务引用，防止被垃圾回收
        _ = task
        logger.info("[4/4] 成长事件已创建")
    except Exception as e:
        # 成长事件创建失败不影响简历上传
        logger.warning("[4/4] 成长事件创建失败: %s", e)

    # 5. 返回结果
    preview = raw_text[:_PREVIEW_LENGTH].replace("\n", " ")
    return {
        "success": True,
        "message": "简历解析成功，已写入 memory.md",
        "preview": preview,
        "content_length": len(markdown_content),
    }
