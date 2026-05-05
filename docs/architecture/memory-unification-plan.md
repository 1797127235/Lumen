# 记忆模块统一方案

**目标**：消灭 payload 猜测、snapshot 短路、多路径不一致，建立类型化事件总线。

---

## 问题诊断

### 根因

系统在两种模式间摇摆：
- **事件驱动投影**：字段级事件 → `_deep_merge` → 结构化 markdown
- **直接写 blob**：`memory_md` 整块快照 → `__memory_md_snapshot` 短路 → 跳过所有合并

四写入路径的 payload 格式没有契约，各路径各自猜测 key 名。

### 具体问题（6 条）

| # | 问题 | 位置 | 严重 |
|---|------|------|------|
| 1 | 简历 blob 只进 memory.md，skills/experiences 永远空 | `profile_service.py:182` | 🔴 |
| 2 | memory_save 工具写 skill 不带 level，永远 default familiar | `pydantic_tools.py:86` | 🟡 |
| 3 | memory_extractor 异步跑，可能重复写 Agent 刚存的事件 | `chat_service.py:141` | 🟡 |
| 4 | 字符限制常量在两个文件各定义一次 | `md_projector:29` / `memory_service:17` | 🟢 |
| 5 | 默认模板在两个文件各定义一次，实现还不同 | `md_projector:345` / `memory_service:68` | 🟢 |
| 6 | memory_extractor payload schema 全凭 LLM 猜 | `memory_extractor:12` | 🟡 |

---

## 目标架构

```
                       ┌─ profile_updated  ──→ ProfilePayload   (结构化字段)
任意写入点 ──→ 统一 ──├─ skill_added       ──→ SkillPayload      (name+level+context)
(简历/Agent/          ├─ experience_added  ──→ ExperiencePayload (title/desc/tech/role)
 提取器/文件)         ├─ preference_learned──→ KeyValuePayload   (key: value)
                       ├─ goal_updated      ──→ KeyValuePayload
                       ├─ status_changed    ──→ KeyValuePayload
                       ├─ decision_made     ──→ DecisionPayload
                       └─ file_ingested     ──→ FilePayload       (审计层,未来扩展)
                                    ↓
                        投影器 → 3 个 .md 文件
                        (不猜 key，不短路，纯类型化合并)
```

**约束**：
- 每个事件类型有对应的 Pydantic Payload schema
- 所有 payload 构造和解析都走 schema.validate()
- 投影器不猜 key 名、不检测字段是否存在——schema 保证形状
- 不再有 `__memory_md_snapshot` 短路
- 简历只是 `file_type="resume"`，和未来文件类型共用入口

---

## 实现步骤

### Step 1：新建 `schemas/memory_events.py` — 类型化 Payload Schema

所有事件 payload 的 Pydantic 定义，一处定义全项目引用。

```python
# ProfilePayload — 字段与 _generate_memory_md 的 consumer 严格对齐
class ProfilePayload(BaseModel):
    # 基础信息
    school_name: str | None = None
    major: str | None = None
    grade: str | None = None
    graduation_year: str | None = None
    school_level: str | None = None
    # 目标方向
    target_direction: str | None = None
    target_company_level: str | None = None
    city: str | None = None
    # 教育背景
    gpa: str | None = None
    ranking: str | None = None
    awards: list[str] | None = None
    # 其他
    bio: str | None = None
    english_level: str | None = None
    expected_salary: str | None = None

class SkillPayload(BaseModel):
    name: str
    level: Literal["familiar", "proficient", "expert"] = "familiar"
    context: str = ""
    source: str = ""

class ExperiencePayload(BaseModel):
    title: str
    description: str = ""
    period: str = ""
    tech_stack: str = ""
    role: str = ""
    source: str = ""

class KeyValuePayload(BaseModel):
    """复用: preference_learned / goal_updated / status_changed"""
    key: str
    value: str

class DecisionPayload(BaseModel):
    title: str
    content: str

class FilePayload(BaseModel):
    """未来扩展: file_ingested"""
    filename: str
    file_type: Literal["resume", "project", "notes", "generic"]
    file_hash: str = ""
    size_bytes: int = 0
    metadata: dict = {}
```

### Step 2：统一常量和模板 — 消除重复定义

```
app/backend/services/memory_limits.py    (新建 ~15 行)
    ├── MEMORY_CHAR_LIMIT = 5000
    ├── SKILLS_CHAR_LIMIT = 2000
    ├── EXPERIENCES_CHAR_LIMIT = 2000
    └── _LIMITS dict

app/backend/services/memory_templates.py (新建 ~50 行)
    ├── memory_default()
    ├── skills_default()
    └── experiences_default()
```

- `md_projector.py` 和 `memory_service.py` 改为 import 这些
- 删除原有的重复定义

### Step 3：重写投影器合并逻辑 — 消灭 snapshot 短路

**`md_projector.py` 改动**：

```python
# 旧：payload guess + snapshot 短路
def _merge_profile_events(events):
    if payload.get("memory_md"): → snapshot 短路
    if payload.get("name"): ...  # 猜 key

# 新：schema 驱动 + 无短路
from app.backend.schemas.memory_events import ProfilePayload, SkillPayload, ExperiencePayload, ...

def _merge_profile_events(events) -> dict:
    profile = {}
    for event in events:
        payload = ProfilePayload.model_validate(_load_payload(event))
        profile = _deep_merge(profile, payload.model_dump(exclude_unset=True))
    # 不再检查 __memory_md_snapshot
    return profile

def _merge_skill_events(events) -> dict[str, dict]:
    skills = {}
    for event in events:
        payload = SkillPayload.model_validate(_load_payload(event))
        skills[payload.name] = payload.model_dump()
    return skills

# 同理 _merge_experience_events → ExperiencePayload
# 同理 _merge_dict_events → KeyValuePayload
# 同理 _merge_decision_events → DecisionPayload
```

**`_generate_memory_md`**：移除 `__memory_md_snapshot` 检查（第 182-184 行），始终从结构化字段构建。

### Step 3a：数据迁移 — 历史 blob 事件转化为结构化事件

**问题**：DB 中已有的 `profile_updated` 事件 payload 含 `memory_md` blob。Step 3 删掉 snapshot 短路后，`ProfilePayload.model_validate()` 会因字段不匹配失败，导致 memory.md 变空。

**策略：上线安全降级 + 手动迁移，两步走**。

#### 第一阶段：投影器兼容 legacy（上线时）

`_merge_profile_events` 增加 legacy 分支。`_extract_fields_from_md` 放在 `memory_service.py` 作为共享工具，`md_projector.py` 和 `profile_service.py` 都 import 它：

```python
def _merge_profile_events(events) -> dict:
    profile = {}
    for event in events:
        payload = _load_payload(event)
        
        # Legacy: payload 含 memory_md blob → 规则提取结构化字段
        if "memory_md" in payload and isinstance(payload["memory_md"], str):
            from app.backend.services.memory_service import _extract_profile_fields
            profile = _deep_merge(profile, _extract_profile_fields(payload["memory_md"]))
            continue
        
        # New: schema 驱动
        profile = _deep_merge(
            profile,
            ProfilePayload.model_validate(payload).model_dump(exclude_none=True)
        )
    return profile
```

**效果**：上线后 memory.md 不空（规则能提取基础字段），技能/经历仍然丢失——只影响已有旧数据。新上传的简历走 Step 4 拆解流程，技能/经历完整。

#### 第二阶段：手动迁移（不做）

不写迁移脚本，不批量调 LLM。旧 blob 事件随时间自然减少（新上传覆盖旧数据时重新生成结构化事件）。

### Step 4：简历拆解 — 从 blob 到结构化事件

**LLM 调用增加**：简历上传从 2 次 LLM 变为 3 次（+1 次结构化拆解）。拆解 prompt ~200 tokens，输出 ~300 tokens，`qwen-plus` 增成本 < ¥0.002/次。可接受。

**改动 `profile_service.py`**：

```python
from pydantic_ai import Agent
from app.backend.agent.llm_router import _get_model_identifier
from app.backend.schemas.memory_events import SkillPayload, ExperiencePayload

class ResumeSkill(BaseModel):
    name: str; level: str = "familiar"; context: str = ""

class ResumeExperience(BaseModel):
    title: str; description: str = ""; period: str = ""
    tech_stack: str = ""; role: str = ""

class ResumeDecomposition(BaseModel):
    skills: list[ResumeSkill] = []
    experiences: list[ResumeExperience] = []

_resume_decompose = Agent(
    _get_model_identifier("skill_analysis"),
    result_type=ResumeDecomposition,
    system_prompt="从简历 markdown 中提取技能和经历。只提取明确提到的。",
)

async def _decompose_resume(markdown: str) -> ResumeDecomposition:
    try:
        result = await _resume_decompose.run(markdown[:5000])
        return result.data
    except Exception:
        return ResumeDecomposition()  # 降级：拆解失败不阻塞
```

**profile 字段来源（选 B：正则提取，不调 LLM）**：

`ResumeDecomposition` 只拆 skills 和 experiences。profile 字段（学校/专业/GPA 等）用 `_extract_fields_from_md` 从 `[2/3]` 的 markdown 正则提取——简历 prompt 要求固定格式（`- 学校：...`），正则零成本、确定性 100%。

```python
# profile_service.py 新增 — 与 Step 3a 的 _extract_fields_from_md 共用
def _extract_profile_fields(md_text: str) -> dict:
    patterns = {
        # 基础字段：单行匹配
        "school_name":        r"- 学校：(.+)",
        "major":              r"- 专业：(.+)",
        "grade":              r"- 年级：(.+)",
        "graduation_year":    r"- 毕业年份：(.+)",
        "school_level":       r"- 学校层次：(.+)",
        "target_direction":   r"- 目标岗位：(.+)",
        "target_company_level": r"- 目标公司类型：(.+)",
        "city":               r"- 意向城市：(.+)",
        "gpa":                r"- GPA：(.+)",
        "ranking":            r"- 排名：(.+)",
        "english_level":      r"- (.+)",   # 跟在 ## 英语水平 后
        "expected_salary":    r"- (.+)",   # 跟在 ## 期望薪资 后
        # 多行字段：需要 re.DOTALL
    }
    fields = {}
    # 单行字段
    for key, pattern in patterns.items():
        m = re.search(pattern, md_text)
        if m:
            val = m.group(1).strip()
            if val and val != "（待填写）":
                fields[key] = val
    # 多行字段：bio 可能跨行，用 re.DOTALL
    m = re.search(r"## 个人简介\s*\n(.+?)(?=\n##|\n---|\Z)", md_text, re.DOTALL)
    if m:
        val = m.group(1).strip()
        if val and val != "（待填写）":
            fields["bio"] = val
    return fields
```

**`process_resume_to_memory`**：
- 保留 `[1/3]` 文本提取（不变）
- 保留 `[2/3]` LLM 生成 markdown（不变）
- **新增** `[2.5/3]`：
  - `_decompose_resume(md)` → skills + experiences（PydanticAI 结构化）
  - `_extract_profile_fields(md)` → profile 字段（正则提取）
- 重写 `[3/3]`：
  - 写 `resume_uploaded` 事件（审计，保留）
  - **不再写 `memory_md` blob**
  - profile 字段 → 写 `profile_updated`（ProfilePayload）
  - skills → 写 `skill_added × N`（SkillPayload）
  - experiences → 写 `experience_added × N`（ExperiencePayload）

### Step 5：修 memory_save 工具 — 按 entity_type 正确映射

entity_type → event_type → payload schema 的对应关系：

```
skills      → skill_added         → SkillPayload(name, level, context, source)
experiences → experience_added    → ExperiencePayload(title, description, period, tech_stack, role, source)
preferences → preference_learned  → KeyValuePayload(key, value)
goals       → goal_updated        → KeyValuePayload(key, value)
status      → status_changed      → KeyValuePayload(key, value)
decisions   → decision_made       → DecisionPayload(title, content)
memory      → 不适用 — 用独立的 update_profile tool（已有，走 ProfilePayload）
```

`memory` 类型不走 `memory_save`。Agent 更新画像结构化字段应该调用已有的 `update_profile` tool（`pydantic_tools.py:126`），它接受 `fields: dict`，映射到 `ProfilePayload`。

**改动 `pydantic_tools.py`**：

```python
async def memory_save(ctx, entity_type, section, content):
    if entity_type == "skills":
        payload = SkillPayload(
            name=section, level="familiar",
            context=content, source="Agent工具",
        ).model_dump()
    elif entity_type == "experiences":
        payload = ExperiencePayload(
            title=section, description=content, source="Agent工具",
        ).model_dump()
    elif entity_type == "decisions":
        payload = DecisionPayload(
            title=section, content=content,
        ).model_dump()
    elif entity_type in ("preferences", "goals", "status"):
        payload = KeyValuePayload(key=section, value=content).model_dump()
    else:
        return f"不支持的类型 {entity_type}，请使用正确的工具"
    # ... create_event_and_project_md(...)
```

### Step 6：约束 memory_extractor — Pydantic 输出 + payload 校验

**改动 `memory_extractor.py`**：

```python
from pydantic import ValidationError
from app.backend.schemas.memory_events import (
    ProfilePayload, SkillPayload, ExperiencePayload,
    KeyValuePayload, DecisionPayload,
)

# event_type → payload schema 映射（与投影器共用同一套定义）
_EVENT_PAYLOAD_MAP = {
    "profile_updated":      ProfilePayload,
    "skill_added":          SkillPayload,
    "skill_level_changed":  SkillPayload,
    "experience_added":     ExperiencePayload,
    "preference_learned":   KeyValuePayload,
    "goal_updated":         KeyValuePayload,
    "status_changed":       KeyValuePayload,
    "decision_made":        DecisionPayload,
}

def _validate_event_payload(event: dict) -> dict | None:
    """校验提取事件的 payload 是否匹配对应 schema，不匹配则丢弃。"""
    schema = _EVENT_PAYLOAD_MAP.get(event["event_type"])
    if schema is None:
        return None
    try:
        validated = schema.model_validate(event.get("payload", {}))
        event["payload"] = validated.model_dump()
        return event
    except ValidationError:
        logger.warning("Extractor event rejected: type=%s", event.get("event_type"))
        return None

class MemoryExtraction(BaseModel):
    events: list[ExtractedEvent] = []

class ExtractedEvent(BaseModel):
    event_type: Literal["profile_updated","skill_added","skill_level_changed",
                         "goal_updated","preference_learned","decision_made",
                         "status_changed","experience_added"]
    payload: dict
    confidence: float = 0.0

_extract_agent = Agent(
    _get_model_identifier("memory_summarize"),
    result_type=MemoryExtraction,
    system_prompt="从对话中提取可长期存储的信息。只提取明确表达的内容。",
)

async def extract_memory_from_conversation(...):
    result = await _extract_agent.run(prompt)
    valid_events = []
    for e in result.data.events:
        if e.confidence < 0.7:
            continue
        validated = _validate_event_payload(e.model_dump())
        if validated:
            valid_events.append(validated)
    return valid_events
```

### Step 7：文件摄取管线（未来扩展占位）

**新建 `services/file_ingestion.py`**（~30 行，占位）：

```python
from app.backend.schemas.memory_events import FilePayload

class FileDecomposer:
    """基类，子类实现 decompose()"""
    async def decompose(self, content: str) -> list[dict]:
        raise NotImplementedError

FILE_DECOMPOSERS = {
    "resume": ResumeDecomposer(),  # 与 Step 4 共用
    # 未来: "project": ProjectDecomposer(), "notes": NotesDecomposer()
}

async def ingest_file(file_type, content, filename, user_id):
    # ① file_ingested 事件
    # ② 类型路由 → decomposer.decompose() → 写结构化事件
    # ③ sync_user_md_projection()
```

### Step 8：清理死代码

| 文件 | 操作 |
|------|------|
| `agent/tools.py` | **删除** (23 行) |
| `profile_service.py:110` | 删 `current_year=` 死参数 |
| `memory_service.py:17-25` | 删重复常量 (import from limits) |
| `memory_service.py:68-120` | 删重复模板 (import from templates) |
| `md_projector.py:29-31` | 删重复常量 |
| `md_projector.py:345-391` | 删重复模板 |

---

## 改动总览

| # | 文件 | 操作 | 估计行数 |
|---|------|------|---------|
| 1 | `schemas/memory_events.py` | **新建** | +120 |
| 2 | `services/memory_limits.py` | **新建** | +15 |
| 3 | `services/memory_templates.py` | **新建** | +50 |
| 4 | `services/md_projector.py` | 重写 merge + legacy 兼容 + 删 snapshot | ~100 改 |
| 5 | `services/profile_service.py` | 加拆解 + 重写事件写入 | +70 改 |
| 6 | `agent/pydantic_tools.py` | 修 memory_save | ~15 改 |
| 7 | `services/memory_extractor.py` | Pydantic 约束 | ~25 改 |
| 8 | `services/memory_service.py` | 删重复，import 新模块 | ~30 改 |
| 9 | `services/file_ingestion.py` | **新建**（可选最后做）| +30 |
| 10 | `agent/tools.py` | **删除** | -23 |

**净增**：~280 行，6 新文件，6 改文件，1 删文件。

---

## 执行顺序

```
Step 1  (schema) ─────── 基础依赖，先建
    ↓
Step 2  (constants) ──── 不依赖 schema
    ↓
Step 3  (projector) ──── 核心重写 + legacy 兼容，依赖 Step 1
    ↓
Step 4  (resume decomp) ─ 依赖 Step 1 schema + Step 3 投影器
    ↓
Step 5  (tools fix) ──── 依赖 Step 1 schema
    ↓
Step 6  (extractor fix) ─ 依赖 Step 1 schema
    ↓
Step 8  (cleanup) ─────── 确认无引用再删
    ↓
Step 9  (file ingestion) ─ 可选最后做
```

Legacy blob 事件用 regex 降级处理（6 行兼容代码），不迁移、不调 LLM。旧 blob 里的基础字段（学校/专业/GPA）正则提取可用，技能/经历仍然丢失——只有新上传的简历走拆解流程才能补全。

---

## 验证

每个 Step 完成后：
- `ruff check` 通过
- `ruff format` 通过
- 受影响文件 `lsp_diagnostics` 干净
- Step 3 特别验证：`_generate_memory_md` 不再有 `__memory_md_snapshot` 分支
