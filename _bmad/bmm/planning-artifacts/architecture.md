---
title: CareerOS Agent 工具层架构设计
version: 1.0
date: 2026-05-05
status: draft
stepsCompleted: [1]
inputDocuments:
  - docs/架构/系统整体架构.md
  - docs/需求/用户画像与场景.md
  - app/backend/agent/pydantic_tools.py
  - app/backend/agent/pydantic_agent.py
  - app/backend/services/chat_service.py
---

# CareerOS Agent 工具层架构设计

## 1. 当前架构分析

### 1.1 现有问题

| 问题 | 影响 | 严重程度 |
|------|------|----------|
| 工具描述太长 | AI 无法正确解析，导致工具调用失败 | 🔴 高 |
| 验证逻辑分散 | grade、target_direction、target_company_level 验证散落各处，难以维护 | 🟡 中 |
| 无 commit | 只 flush 不 commit，用户以为保存了实际没保存 | 🔴 高 |
| 错误静默 | 验证失败只 warning，AI 不知道，用户不知道 | 🔴 高 |
| grade_map 重复 | get_profile 和 update_profile 各写一遍 | 🟢 低 |
| 系统提示词与工具描述重复 | 两处维护，容易不一致 | 🟡 中 |

### 1.2 当前工具层结构

```
pydantic_tools.py
├── get_profile: 读取用户画像
├── update_profile: 更新用户画像（支持 9 个字段）
└── diagnose_jd: JD 对比分析
```

### 1.3 当前调用流程

```
用户输入 → AI → 工具调用 → 验证 + 存储 → 返回结果
```

**问题**：验证失败 → 静默丢弃 → AI 以为成功 → 用户以为保存

---

## 2. 架构改进方案

### 2.1 核心原则

1. **单一职责**：每个工具只做一件事
2. **显式错误**：验证失败必须返回错误给 AI，让 AI 重试或问用户
3. **事务保护**：工具内部 commit，确保数据一致性
4. **描述简洁**：工具描述不超过 3 行，AI 能正确理解

### 2.2 新工具层结构

```
pydantic_tools.py
├── get_profile: 读取用户画像（不变）
├── update_basic_info: 更新基础信息（学校、专业、年级）
├── update_target: 更新目标信息（方向、公司级别）
├── update_extended: 更新扩展信息（简介、城市、薪资、英语）
└── diagnose_jd: JD 对比分析（不变）
```

### 2.3 新调用流程

```
用户输入 → AI → 工具调用 → 验证 → 失败则返回错误给 AI 重试
                           → 成功则 commit + 返回确认
```

---

## 3. 详细设计

### 3.1 工具职责划分

| 工具 | 职责 | 字段 | 触发条件 |
|------|------|------|----------|
| `get_profile` | 读取用户画像 | 所有字段 | 用户明确要求「查看我的画像」 |
| `update_basic_info` | 更新基础信息 | school_name, major, grade | 用户提到学校、专业、年级 |
| `update_target` | 更新目标信息 | target_direction, target_company_level | 用户提到目标方向、目标公司 |
| `update_extended` | 更新扩展信息 | bio, city, expected_salary, english_level | 用户提到个人简介、城市等 |

### 3.2 验证逻辑设计

```python
# 统一验证函数
def validate_grade(value: str) -> tuple[bool, str]:
    """验证年级，返回 (是否有效, 错误信息或映射值)"""
    grade_map = {
        "大一": "freshman", "大二": "sophomore", "大三": "junior",
        "大四": "senior", "研一": "graduate1", "研二": "graduate2", "研三": "graduate3"
    }
    valid_grades = {"freshman", "sophomore", "junior", "senior", "graduate1", "graduate2", "graduate3"}
    
    # 尝试直接映射
    if value in grade_map:
        return True, grade_map[value]
    
    # 尝试英文值
    if value.lower() in valid_grades:
        return True, value.lower()
    
    return False, f"无效的年级：{value}。支持的值：大一/大二/大三/大四/研一/研二/研三"

def validate_target_direction(value: str) -> tuple[bool, str]:
    """验证目标方向，返回 (是否有效, 错误信息或映射值)"""
    direction_map = {
        "后端": "backend", "前端": "frontend", "算法": "algorithm",
        "AI": "ai", "测试": "test", "运维": "devops", "产品": "product"
    }
    
    if value in direction_map:
        return True, direction_map[value]
    
    return False, f"无效的目标方向：{value}。支持的值：后端/前端/算法/AI/测试/运维/产品"

def validate_company_level(value: str) -> tuple[bool, str]:
    """验证目标公司，返回 (是否有效, 错误信息或映射值)"""
    level_map = {
        "大厂": "top", "中厂": "major", "小厂": "medium", "国企": "state_owned"
    }
    
    if value in level_map:
        return True, level_map[value]
    
    return False, f"无效的目标公司：{value}。支持的值：大厂/中厂/小厂/国企"
```

### 3.3 错误处理设计

```python
@agent.tool
async def update_basic_info(
    ctx: RunContext[CareerOSDeps],
    school_name: str | None = None,
    major: str | None = None,
    grade: str | None = None,
) -> str:
    """更新用户基础信息（学校、专业、年级）。当用户提到这些信息时调用。"""
    logger.info("工具调用: update_basic_info, user_id=%s", ctx.deps.user_id)
    
    db = ctx.deps.db
    user_id = ctx.deps.user_id
    
    # 获取或创建用户画像
    result = await db.execute(select(UserProfile).where(UserProfile.user_id == user_id))
    profile = result.scalar_one_or_none()
    if not profile:
        profile = UserProfile(user_id=user_id)
        db.add(profile)
    
    updated_fields = []
    errors = []
    
    # 更新学校
    if school_name is not None:
        profile.school_name = school_name
        updated_fields.append("学校")
    
    # 更新专业
    if major is not None:
        profile.major = major
        updated_fields.append("专业")
    
    # 更新年级（需要验证）
    if grade is not None:
        valid, result = validate_grade(grade)
        if valid:
            profile.grade = result
            updated_fields.append("年级")
        else:
            errors.append(result)
    
    # 如果有错误，返回错误信息给 AI
    if errors:
        return f"更新失败：{'; '.join(errors)}"
    
    # 提交事务
    await db.commit()
    
    if updated_fields:
        return f"已更新：{', '.join(updated_fields)}"
    else:
        return "没有需要更新的字段。"
```

### 3.4 系统提示词设计

```python
system_prompt = """
你是 CareerOS，一个面向中国 CS 学生的 AI 职业规划助手。

## 用户画像规则

用户画像已自动加载到上下文中，【不要】主动调用 get_profile 工具。

当用户提到个人信息时，【必须】调用相应工具保存：
- 学校、专业、年级 → 调用 update_basic_info
- 目标方向、目标公司 → 调用 update_target
- 个人简介、城市等 → 调用 update_extended

工具调用失败时，告诉用户具体原因并请用户重新提供。

## 回复风格

- 使用中文
- 结构化输出（使用 Markdown）
- 给出具体可执行的建议
- 鼓励而非说教
"""
```

---

## 4. 实施计划

### 4.1 第一阶段：重构工具层

1. 创建 `validation.py` 统一验证逻辑
2. 拆分 `update_profile` 为三个工具
3. 添加事务保护（commit）
4. 简化工具描述

### 4.2 第二阶段：优化系统提示词

1. 明确工具调用时机
2. 添加错误处理指引
3. 减少与工具描述的重复

### 4.3 第三阶段：测试验证

1. 单元测试：验证逻辑
2. 集成测试：工具调用流程
3. 端到端测试：完整对话流程

---

## 5. 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 工具拆分后 AI 调用混乱 | 工具调用失败率上升 | 简化工具描述，明确触发条件 |
| 验证逻辑过于严格 | 用户体验下降 | 提供默认值，允许部分更新 |
| 事务提交失败 | 数据丢失 | 添加重试机制，记录失败日志 |

---

## 6. 开放问题

1. 是否需要支持批量更新（一次调用更新多个字段）？
2. 是否需要添加撤销功能（undo）？
3. 是否需要记录字段修改历史？

---

## 7. 下一步

- [ ] 与用户确认架构方案
- [ ] 创建 validation.py
- [ ] 重构工具层
- [ ] 更新系统提示词
- [ ] 测试验证
