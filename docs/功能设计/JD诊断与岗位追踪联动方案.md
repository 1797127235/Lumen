# JD 诊断与岗位追踪联动方案

> 问题：JD 诊断和岗位追踪是两条孤立的线。用户在 `/jd` 页面诊断完，想加到看板必须重新粘贴 JD、重新诊断。本文档定义如何打通。

---

## 一、现状：两条孤立的线

```
路径 A（JD 诊断页）：
粘贴 JD → POST /api/jd/diagnose → 看报告 → 结束
                                         ↑
                                    诊断存在 jd_diagnoses 表，跟看板没关系

路径 B（岗位追踪页）：
新增岗位 → 又粘贴一遍 JD → POST /api/targets → 重复诊断 → 建卡
                      或
新增岗位 → 不贴 JD → 手动建卡 → 无诊断 → 无建议
```

**问题**：
- 路径 A 诊断完了，用户想加到看板 → 必须重新粘贴 JD、重新诊断（浪费时间 + API 调用）
- 路径 B 手动建卡 → `agent_advice` 不生成（代码卡了 `diagnosis is not None` 检查）
- 两个页面各做各的，没有数据流转

---

## 二、目标：三条路径汇入一个入口

```
路径 A（JD 诊断 → 加入看板）：
粘贴 JD → 诊断 → 看报告 → 点"加入看板" → 填公司+岗位 → POST /api/targets { diagnosis_id }
                                                                          ↓
                                                                    复用已有诊断，不重复调 LLM

路径 B（看板直接建卡 + 贴 JD）：
新增岗位 → 贴 JD → POST /api/targets { jd_text } → 同步诊断 → 建卡

路径 C（看板手动建卡）：
新增岗位 → 不贴 JD → POST /api/targets → 建卡（无诊断，有建议）
```

**三条路径最终都走到 `create_target()`，统一处理。**

---

## 三、后端改动

### 3.1 Schema 加 `diagnosis_id` + 校验

```python
# app/backend/schemas/target.py

from pydantic import BaseModel, Field, model_validator

class TargetCreate(BaseModel):
    company: str = Field(..., min_length=1, max_length=100)
    title: str = Field(..., min_length=1, max_length=100)
    location: str | None = None
    salary: str | None = None
    jd_text: str | None = None
    jd_url: str | None = None
    diagnosis_id: str | None = None   # 新增：复用已有诊断
    notes: str | None = None

    @model_validator(mode="after")
    def _check_diagnosis_source(self) -> "TargetCreate":
        if self.diagnosis_id and self.jd_text:
            raise ValueError("diagnosis_id 和 jd_text 不能同时传")
        return self
```

**校验规则**：
- `diagnosis_id` 和 `jd_text` 二选一，都传 → **422 报错**（不静默忽略）
- 都不传 → 手动建卡（路径 C）

### 3.2 Service 层：三条路径统一处理

```python
# app/backend/services/target_service.py

async def create_target(
    db: AsyncSession, user_id: str, req: TargetCreate
) -> TargetDetail:
    """新增岗位。支持三种模式：
    1. diagnosis_id — 复用已有诊断（从 JD 报告页过来）
    2. jd_text — 同步诊断（从看板直接建卡）
    3. 都没有 — 手动建卡（无诊断）
    """
    diagnosis_id: str | None = None
    match_score: int | None = None
    diagnosis_dict: dict | None = None
    jd_text: str | None = req.jd_text  # 默认用传入的 jd_text

    if req.diagnosis_id:
        # 路径 A：复用已有诊断
        diagnosis = await db.get(JDDiagnosis, req.diagnosis_id)
        if not diagnosis or diagnosis.user_id != user_id:
            raise HTTPException(
                status_code=404, detail="诊断不存在或无权限"
            )
        diagnosis_id = diagnosis.diagnosis_id
        match_score = diagnosis.overall_score
        diagnosis_dict = _diagnosis_to_dict(diagnosis)
        jd_text = diagnosis.jd_text  # 从诊断复制 JD 原文
    elif req.jd_text and req.jd_text.strip():
        # 路径 B：有 JD 文本则诊断
        diag = await diagnose_jd(db, user_id, req.jd_text)
        diagnosis_id = diag.diagnosis_id
        match_score = diag.overall_score
        diagnosis_dict = diag.model_dump()
    # 路径 C：都没有，手动建卡

    sort_order = await _next_sort_order(db, user_id, "interested")

    target = JobTarget(
        user_id=user_id,
        company=req.company,
        title=req.title,
        location=req.location,
        salary=req.salary,
        jd_text=jd_text,  # 路径 A 时从诊断复制，路径 B 时用传入值，路径 C 时为 null
        jd_url=req.jd_url,
        status="interested",
        diagnosis_id=diagnosis_id,
        match_score=match_score,
        notes=req.notes,
        sort_order=sort_order,
        # status_changed_at 不显式赋值，沿用模型的 server_default=func.now()
    )
    db.add(target)
    await db.flush()
    return _to_detail(target, diagnosis_dict)
```

**关键改动**：
- 路径 A：诊断不存在或不归属 → **404 报错**（不静默吞掉）
- 路径 A：从 `diagnosis.jd_text` 复制到 `target.jd_text`
- `status_changed_at` 删掉 Python 端赋值，沿用 `server_default=func.now()`（与 `created_at` 同源）

### 3.3 去掉 `agent_advice` 的 diagnosis 限制

**router 层**：
```python
# routers/targets.py — POST
detail = await create_target(db, user_id, req)
# 改前：if detail.diagnosis is not None:
# 改后：无条件生成
background_tasks.add_task(generate_advice, detail.target_id, user_id)
```

**service 层 — update_target**：
```python
# 改前：needs_advice = status_changed and target.diagnosis_id is not None
# 改后：
needs_advice = status_changed
```

**service 层 — generate_advice**：
```python
# 改前（line 211）：
if not target or not target.diagnosis_id:
    return

# 改后：
if not target:
    return

# diagnosis 查询变可选：
diagnosis = None
if target.diagnosis_id:
    diagnosis = (
        await db.execute(
            select(JDDiagnosis).where(
                JDDiagnosis.diagnosis_id == target.diagnosis_id
            )
        )
    ).scalar_one_or_none()

profile_summary = await _load_profile_summary(db, user_id)
advice = await _call_advice_llm(target, diagnosis, profile_summary)
```

### 3.4 `_call_advice_llm` 签名改 diagnosis 可空 + 双 prompt

```python
async def _call_advice_llm(
    target: JobTarget,
    diagnosis: JDDiagnosis | None,  # 改为可空
    profile_summary: str,
) -> str:
    """构造 prompt 并调 LLM。失败时返回空串。"""

    now = datetime.now(timezone.utc)
    created = target.created_at
    if created and created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    days_since_create = (now - created).days if created else 0

    status_label = _STATUS_LABELS.get(target.status, target.status)
    if target.status == "interview" and target.interview_round:
        status_label = f"{status_label}（{target.interview_round}）"

    if diagnosis:
        # 有诊断：完整 prompt
        gaps = diagnosis.get_skill_gaps()
        high = [g["skill"] for g in gaps if g.get("priority") == "high"]
        others = [g["skill"] for g in gaps if g.get("priority") != "high"]
        top_gaps = "、".join((high + others)[:3]) or "无"

        prompt = _ADVICE_PROMPT_WITH_DIAGNOSIS.format(
            profile_summary=profile_summary,
            company=target.company,
            title=target.title,
            status_label=status_label,
            days_since_create=days_since_create,
            match_score=target.match_score if target.match_score is not None else 0,
            top_gaps=top_gaps,
        )
    else:
        # 无诊断：精简 prompt（不提匹配分和缺口）
        prompt = _ADVICE_PROMPT_NO_DIAGNOSIS.format(
            profile_summary=profile_summary,
            company=target.company,
            title=target.title,
            status_label=status_label,
            days_since_create=days_since_create,
        )

    text = await llm_chat(
        task_type="general_chat",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=200,
    )
    return text.strip()
```

**双 prompt 模板**：
```python
_ADVICE_PROMPT_WITH_DIAGNOSIS = """你是求职陪跑教练。基于以下信息，给一句话行动建议（≤50字）。

【用户画像】
{profile_summary}

【岗位】{company} - {title}
【当前状态】{status_label}
【距创建】{days_since_create}天
【诊断要点】匹配 {match_score}/100，主要缺口：{top_gaps}

只输出建议本身，不要解释，不要前缀。"""

_ADVICE_PROMPT_NO_DIAGNOSIS = """你是求职陪跑教练。基于以下信息，给一句话行动建议（≤50字）。

【用户画像】
{profile_summary}

【岗位】{company} - {title}
【当前状态】{status_label}
【距创建】{days_since_create}天

只输出建议本身，不要解释，不要前缀。"""
```

---

## 四、前端改动

### 4.1 `JDReport.tsx` 加"加入看板"按钮

```
┌─────────────────────────────────────────┐
│  ← 返回 JD 诊断          [重新诊断] [加入看板] │
│                                          │
│  字节跳动 · 后端开发工程师                  │
│  ...报告内容...                           │
└─────────────────────────────────────────┘
```

点击"加入看板"弹出 Dialog：

```
┌────────────────────────────────┐
│  加入看板                        │
│                                 │
│  公司 *  [                 ]    │  ← 留空，用户填写（jd_title 不可拆）
│  岗位 *  [后端开发工程师     ]    │  ← 预填 jd_title（可改）
│  城市    [                 ]    │
│  薪资    [                 ]    │
│  备注    [                 ]    │
│                                 │
│       [取消]  [加入看板]         │
└────────────────────────────────┘
```

**字段说明**：
- `company`：留空让用户填写。`jd_title` 是单字符串（如"字节跳动后端开发工程师"），无法可靠拆出公司名
- `title`：预填 `jd_title`，用户可改

**提交**：`POST /api/targets { company, title, diagnosis_id: data.diagnosis_id }`

**跳转**：成功后 `navigate('/targets')`

**幂等处理**（V1 简化）：
- 同一 `diagnosis_id` 可以重复建卡（DB 无唯一约束）
- 按钮点过一次后，状态变为"已加入看板"并禁用，提供"去看板查看"链接
- 前端用 `useState` 跟踪是否已加入（不查后端）

### 4.2 `api.ts` 类型更新

```typescript
export type TargetCreatePayload = {
  company: string;
  title: string;
  location?: string | null;
  salary?: string | null;
  jd_text?: string | null;
  jd_url?: string | null;
  diagnosis_id?: string | null;  // 新增
  notes?: string | null;
};
```

---

## 五、数据流图

```
                    ┌──────────────┐
                    │   JD 诊断页   │
                    │   (/jd)      │
                    └──────┬───────┘
                           │ POST /api/jd/diagnose
                           ▼
                    ┌──────────────┐
                    │   诊断报告页   │
                    │   (/jd/:id)  │
                    └──────┬───────┘
                           │ 点"加入看板"
                           │ POST /api/targets { diagnosis_id }
                           ▼
┌──────────────┐    ┌──────────────┐
│  看板新增弹窗  │───→│  create_target │
│  (/targets)  │    │  (统一入口)    │
└──────────────┘    └──────┬───────┘
  POST /api/targets        │
  { jd_text 或 空 }         │
                           ▼
                    ┌──────────────┐
                    │   job_targets │
                    │   (看板卡片)   │
                    └──────────────┘
```

---

## 六、边界情况

| 场景 | 处理 | 错误码 |
|------|------|--------|
| `diagnosis_id` 不存在 | **报错**，不静默吞掉 | 404 "诊断不存在或无权限" |
| `diagnosis_id` 不属于当前用户 | **报错**，不静默吞掉 | 404 "诊断不存在或无权限" |
| `diagnosis_id` 和 `jd_text` 都传 | **报错**，不静默忽略 | 422 "diagnosis_id 和 jd_text 不能同时传" |
| `diagnosis_id` 和 `jd_text` 都不传 | 手动建卡，无诊断 | 200 |
| 无诊断时生成 agent_advice | 用精简 prompt（不提匹配分和缺口） | 200 |
| 从诊断建卡时 JD 原文 | 从 `diagnosis.jd_text` 复制到 `target.jd_text` | 200 |
| 同一 diagnosis_id 重复建卡 | V1 允许，前端按钮置灰提示"已加入" | 200 |

---

## 七、与现有功能的关系

### 7.1 `/targets` 对话框的 jd_text 字段保留

V1 不砍 `/targets` 新增弹窗的 JD 文本字段。理由：
- 路径 B（直接贴 JD 建卡）是快捷方式，不需要先去 `/jd` 页面
- 砍掉会增加操作步骤（先去诊断 → 再来建卡）
- V2 可以考虑引导用户先诊断再建卡，但 V1 不做

### 7.2 重复建卡的取舍

V1 允许同一 `diagnosis_id` 建多张卡。理由：
- 用户可能想针对同一 JD 投不同岗位（如"后端开发"和"全栈开发"）
- DB 加唯一约束会限制这种用法
- 前端按钮置灰是软提示，不强制

---

## 八、实施顺序

| 步骤 | 内容 | 依赖 |
|------|------|------|
| 1 | `schemas/target.py` 加 `diagnosis_id` + `model_validator` | 无 |
| 2 | `services/target_service.py` 改 `create_target`（路径 A 404 报错 + 复制 jd_text） | 1 |
| 3 | `services/target_service.py` 改 `generate_advice`（删 diagnosis_id 检查） | 无 |
| 4 | `services/target_service.py` 改 `_call_advice_llm`（diagnosis 可空 + 双 prompt） | 3 |
| 5 | `routers/targets.py` 改 POST（无条件调 generate_advice） | 无 |
| 6 | `routers/targets.py` 改 PATCH（needs_advice 去掉 diagnosis 检查） | 无 |
| 7 | `JDReport.tsx` 加"加入看板"按钮 + Dialog（company 留空，title 预填） | 1 |
| 8 | `api.ts` 加 `diagnosis_id` 到 `TargetCreatePayload` | 无 |

步骤 1-6 是后端，步骤 7-8 是前端。可以并行做。
