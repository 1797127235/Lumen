"""记忆文件默认模板（一处定义，全项目引用）。"""

from __future__ import annotations

from datetime import datetime


def memory_default() -> str:
    date = datetime.now().strftime("%Y-%m-%d")
    return f"""# 用户核心记忆

> 这个文件由 AI 自动管理，记录用户的核心信息。
> 每次对话开始时会自动注入到 system prompt。

## 基础信息
- 学校：（待填写）
- 专业：（待填写）
- 年级：（待填写）
- 毕业年份：（待填写）
- 学校层次：（待填写）

## 目标方向
- 目标岗位：（待填写）
- 目标公司类型：（待填写）
- 意向城市：（待填写）

## 当前状态
- 正在学习：（待填写）
- 正在准备：（待填写）
- 焦虑程度：（待填写）

---
*最后更新：{date}*
"""


def skills_default() -> str:
    date = datetime.now().strftime("%Y-%m-%d")
    return f"""# 技能列表

> 记录用户的技能状态，用于能力评估和学习建议。

## 已掌握技能
（待填写）

---
*最后更新：{date}*
"""


def experiences_default() -> str:
    date = datetime.now().strftime("%Y-%m-%d")
    return f"""# 经历列表

> 记录用户的项目、实习、竞赛和其它成长经历。

（待填写）

---
*最后更新：{date}*
"""
