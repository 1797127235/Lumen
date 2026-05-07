# Lumen 记忆层架构

## 1. 设计目标

Lumen 是面向学生的职业 AI 伴侣，需要长期"认识"用户。记忆层要支撑以下能力：

- 回答关于用户的事实问题（"我会什么技能"、"我大二做了什么"）
- 处理任意形式的长文档（简历、论文、笔记、外部参考资料）
- 跨内容做语义匹配（"我适合这个岗位吗"、"和我类似的学长去了哪"）
- 追踪成长轨迹与情绪状态（"我的方向怎么演化的"、"我最近焦虑什么"）
- 理解人脉关系与外部信息（导师建议、行业情报、学长经验）
- 支撑 Agent 在每次对话中获取相关上下文

**非目标**：成为通用记忆数据库；做实时高并发查询；支持多租户。

---

## 2. 核心原则

### 2.1 存储匹配数据形态

不同形态的数据用不同存储，不强行统一抽象：

- **已结构化** → SQLite（精确查询、JOIN、时序聚合）
- **长文本 / 文档** → Cognee（语义检索、知识图谱、跨文档关联）
- **原始档案** → 文件系统（永远是真相源）

### 2.2 单一职责

每层只做一件事：
- 存储层只存取，不做业务逻辑
- 摄入层只把外部输入转成内部表示
- 查询层只编排，不发明新的存储抽象
- Agent 接口只暴露业务级方法，不暴露底层

### 2.3 在应用层组合，不在存储层组合

需要"结构化事实 + 文档语义"一起用的查询，由查询层从两边各拿数据再组合，**不试图把两者塞进同一个存储抽象**。

### 2.4 显式胜于通用

Agent 不调通用的 `recall(query)`，而调有明确语义的方法。每个方法内部决定从哪些层取数，调用方不感知底层。

### 2.5 开放的内容类型

记忆层不预设"只处理简历和面试"。任何学生生活中有意义的内容都是合法输入——课程笔记、比赛复盘、导师反馈、情绪日记、公司调研……每种内容类型是一个 `ingest/` 模块，架构不设上限。

---

## 3. 整体架构

```
                    ┌─────────────────────────┐
                    │      Agent / 业务层       │
                    └─────────────────────────┘
                                ↓
                    ┌─────────────────────────┐
                    │   memory/facade.py       │  ← 唯一入口
                    └─────────────────────────┘
                                ↓
        ┌───────────────────────┼───────────────────────┐
        ↓                       ↓                       ↓
   ┌─────────┐             ┌─────────┐             ┌──────────┐
   │  query/ │             │ ingest/ │             │extractors│
   │ 查询编排 │             │ 摄入管线 │             │ LLM 抽取 │
   └─────────┘             └─────────┘             └──────────┘
        ↓                       ↓                       ↓
   ┌──────────────────────────────────────────────────────┐
   │                    stores/                           │
   │  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐  │
   │  │ relational   │ │  semantic    │ │  documents   │  │
   │  │ (SQLite)     │ │  (Cognee)    │ │ (filesystem) │  │
   │  └──────────────┘ └──────────────┘ └──────────────┘  │
   └──────────────────────────────────────────────────────┘
```

---

## 4. 内容类型全景

以下是记忆层支持的所有内容类型，按学生生活场景组织。

### 4.1 个人档案与成长事件

| 内容 | 说明 |
|---|---|
| 基本画像 | 学校、专业、年级、GPA |
| 技能清单 | 技能名称、掌握程度、习得时间 |
| 工作 / 实习经历 | 公司、岗位、时间、职责 |
| 学术经历 | 科研、论文、导师 |
| 荣誉与证书 | 奖学金、竞赛奖项、证书 |

### 4.2 学习与知识积累

| 内容 | 说明 |
|---|---|
| 课程笔记 | 上课记录、知识点整理 |
| MOOC / 在线课程 | 完成了哪个课、学到了什么 |
| 读书笔记 | 书名、核心观点、对自己的启发 |
| 论文阅读笔记 | 读了哪篇、核心贡献、与自己方向的关系 |
| 技术文章收藏 | 链接 + 个人注解 |
| 学期 / 阶段总结 | 这段时间学了什么、有什么收获 |

### 4.3 项目与创作

| 内容 | 说明 |
|---|---|
| GitHub 项目 | README、技术栈、复盘反思 |
| 比赛经历 | 题目、方案、结果、教训 |
| 研究成果 | 论文草稿、实验记录、结论 |
| 设计作品 | 作品描述、设计思路、反馈 |
| 个人博客文章 | 写了什么、给谁看、反响如何 |
| 开源贡献 | 项目名、贡献内容、社区反馈 |

### 4.4 求职过程

| 内容 | 说明 |
|---|---|
| 简历版本 | 不同时期的简历文件 |
| JD 收藏 | 感兴趣的岗位描述 |
| 投递记录 | 投了哪些、状态如何 |
| 面试记录 | 公司、岗位、考察点、结果、反馈 |
| Offer 信息 | 薪资、福利、最终决策 |

### 4.5 人脉与关系

| 内容 | 说明 |
|---|---|
| Mentor / 导师反馈 | 他们说了什么、给了什么建议 |
| 校友对话记录 | 和谁聊了什么、得到了什么信息 |
| 推荐信草稿 | 谁写的、强调了哪些优点 |
| 同伴合作记录 | 和谁一起做了什么项目、协作感受 |

### 4.6 外部参考与市场情报

| 内容 | 说明 |
|---|---|
| 学长经验贴 | 背景、成长路径、关键决策、去向 |
| 公司调研笔记 | 文化、技术栈、面试风格、HC 情况 |
| 行业报告 / 趋势 | 技能需求变化、岗位热门程度 |
| 薪资 / Offer 情报 | 不同公司的 package 信息 |
| 职场建议文章 | 值得收藏的方法论内容 |

### 4.7 规划与决策

| 内容 | 说明 |
|---|---|
| 职业规划文档 | 3 年目标、路径拆解 |
| 学期 / 月度目标 | 要做什么、完成情况 |
| 关键决策记录 | 在 A 和 B 之间怎么想的、最终选了什么 |
| 选项对比分析 | 列了哪些 pro/con、权重怎么定的 |

### 4.8 情绪与反思

| 内容 | 说明 |
|---|---|
| 日记 / 周记 | 最近状态、焦虑点、开心的事 |
| 复盘记录 | 这次做得好的、下次要改的 |
| 随手想法 | 突然冒出来的想法，怕忘 |
| 情绪记录 | 对某件事的感受（选填，完全私密）|
| 价值观反思 | 什么对自己最重要、正在发生什么变化 |

### 4.9 对话历史

| 内容 | 说明 |
|---|---|
| 与 Lumen 的对话 | 历史会话、关键问答 |
| 对话摘要 | 自动生成，记录每次对话的核心信息 |

---

## 5. 数据形态与存储映射

每种内容类型按数据形态分流到不同存储，同一内容可同时进多层。

| 内容类型 | 文件存储 | SQLite（结构化） | Cognee（语义） |
|---|:---:|:---:|:---:|
| **个人档案 / 技能 / 经历** | – | ✅ GrowthEvent | – |
| **荣誉 / 证书** | – | ✅ awards 表 | – |
| **课程笔记** | – | ✅ 元数据 | ✅ 内容全文 |
| **MOOC 完成记录** | – | ✅ learning_records | – |
| **读书笔记** | – | ✅ 元数据 | ✅ 内容 |
| **论文阅读笔记** | – | ✅ 元数据 | ✅ 内容 |
| **技术文章收藏** | – | ✅ 元数据 | ✅ 正文 + 注解 |
| **GitHub 项目** | – | ✅ projects 表 | ✅ README + 复盘 |
| **比赛经历** | – | ✅ competitions 表 | ✅ 复盘原文 |
| **论文 / 研究成果** | ✅ PDF | ✅ 元数据 | ✅ 正文 chunks |
| **设计作品** | ✅ 源文件 | ✅ 元数据 | ✅ 描述 |
| **博客文章** | – | ✅ 元数据 | ✅ 正文 |
| **简历** | ✅ PDF | ✅ doc 元数据 | ✅ chunks + 图谱 |
| **JD** | ✅ 原文 | ✅ doc + 要求列表 | ✅ chunks + 图谱 |
| **投递记录** | – | ✅ applications 表 | – |
| **面试记录** | – | ✅ interviews 元数据 | ✅ 反馈原文 |
| **Offer 信息** | – | ✅ offers 表 | – |
| **Mentor 反馈** | – | ✅ 元数据 | ✅ 反馈原文 |
| **校友对话记录** | – | ✅ 元数据 | ✅ 对话摘要 |
| **推荐信** | ✅ 文件 | ✅ 元数据 | ✅ 正文 |
| **学长经验贴** | ✅ 原文 | ✅ 元数据（背景、去向）| ✅ 全文 |
| **公司调研笔记** | – | ✅ companies 元数据 | ✅ 笔记内容 |
| **行业报告** | ✅ 原文 | ✅ 元数据 | ✅ chunks |
| **薪资情报** | – | ✅ salary_data 表 | – |
| **职业规划** | – | ✅ 元数据 | ✅ 规划原文 |
| **目标 / 里程碑** | – | ✅ goals 表 | – |
| **决策记录** | – | ✅ decisions 元数据 | ✅ 思考原文 |
| **日记 / 周记** | – | ✅ 元数据 | ✅ 内容 |
| **复盘记录** | – | ✅ 元数据 | ✅ 内容 |
| **随手想法** | – | – | ✅ 内容（短文本直接进）|
| **对话摘要** | – | ✅ conversations 表 | ✅ 摘要 chunks |

**判断规则**：
- 有明确字段、能写出 SQL JOIN？→ SQLite
- 是用户上传的原始文件？→ 文件存储
- 是长文本、需要"找相关段落"？→ Cognee
- 三个都满足 → 三层都进，各放对应形态的部分

---

## 6. 模块结构

```
app/backend/memory/
│
├── facade.py                         # 唯一对外入口
│
├── stores/                           # 底层存储抽象
│   ├── __init__.py
│   ├── relational.py                 # SQLite 操作（薄封装）
│   ├── semantic.py                   # Cognee 封装（remember/recall/cognify）
│   └── documents.py                  # 文件存储 + documents 表
│
├── ingest/                           # 摄入管线（按内容类型）
│   ├── __init__.py
│   │
│   ├── # ── 个人档案 ──
│   ├── event.py                      # GrowthEvent → SQLite
│   ├── award.py                      # 荣誉/证书 → SQLite
│   │
│   ├── # ── 学习 ──
│   ├── course_note.py                # 课程笔记 → SQLite + Cognee
│   ├── mooc.py                       # 在线课程完成 → SQLite
│   ├── book_note.py                  # 读书笔记 → SQLite + Cognee
│   ├── paper_note.py                 # 论文阅读笔记 → SQLite + Cognee
│   ├── article.py                    # 文章收藏 → SQLite + Cognee
│   ├── learning_summary.py           # 阶段总结 → SQLite + Cognee
│   │
│   ├── # ── 项目与创作 ──
│   ├── project.py                    # 项目（GitHub/比赛/研究）→ SQLite + Cognee
│   ├── paper.py                      # 论文/研究成果 → file + SQLite + Cognee
│   ├── portfolio.py                  # 设计作品 → file + SQLite + Cognee
│   ├── blog_post.py                  # 博客文章 → SQLite + Cognee
│   │
│   ├── # ── 求职 ──
│   ├── resume.py                     # 简历 → file + SQLite + Cognee
│   ├── jd.py                         # JD → file + Cognee + 抽取要求
│   ├── application.py                # 投递记录 → SQLite
│   ├── interview.py                  # 面试记录 → SQLite + Cognee
│   ├── offer.py                      # Offer 信息 → SQLite
│   │
│   ├── # ── 人脉 ──
│   ├── mentor_feedback.py            # Mentor 反馈 → SQLite + Cognee
│   ├── alumni_conversation.py        # 校友对话 → SQLite + Cognee
│   ├── recommendation.py             # 推荐信 → file + SQLite + Cognee
│   │
│   ├── # ── 外部参考 ──
│   ├── reference_post.py             # 学长经验贴 → file + SQLite + Cognee
│   ├── company_research.py           # 公司调研 → SQLite + Cognee
│   ├── industry_report.py            # 行业报告 → file + SQLite + Cognee
│   ├── salary_data.py                # 薪资情报 → SQLite
│   │
│   ├── # ── 规划与决策 ──
│   ├── career_plan.py                # 职业规划 → SQLite + Cognee
│   ├── goal.py                       # 目标/里程碑 → SQLite
│   ├── decision.py                   # 决策记录 → SQLite + Cognee
│   │
│   ├── # ── 情绪与反思 ──
│   ├── journal.py                    # 日记/周记 → SQLite + Cognee
│   ├── retrospective.py              # 复盘 → SQLite + Cognee
│   ├── quick_note.py                 # 随手想法 → Cognee（短文本直接入）
│   │
│   └── # ── 对话 ──
│       conversation.py               # 对话归档 → SQLite + Cognee
│
├── query/                            # 查询编排（按 Agent 问题类型）
│   ├── __init__.py
│   │
│   ├── # ── 关于自己 ──
│   ├── profile.py                    # "我是谁" → 结构化画像快照
│   ├── skills.py                     # "我会什么" → 技能树查询
│   ├── experiences.py                # "我做过什么" → 经历聚合
│   ├── achievements.py               # "我有什么成果" → 项目/荣誉
│   ├── narrative.py                  # "我的成长轨迹" → 时序聚合
│   │
│   ├── # ── 求职 ──
│   ├── match.py                      # "我适合这个 JD 吗" → 简历×JD 分析
│   ├── application_status.py         # "我投了哪些、状态如何" → 投递汇总
│   ├── interview_review.py           # "我面试总挂在哪" → 面试复盘
│   │
│   ├── # ── 外部参考 ──
│   ├── similar_paths.py              # "和我类似的学长去了哪" → 经验对照
│   ├── company_intel.py              # "这家公司怎么样" → 调研聚合
│   ├── market_pulse.py               # "现在什么技能最火" → 市场趋势
│   │
│   ├── # ── 学习与规划 ──
│   ├── learning_progress.py          # "我最近在学什么" → 学习进度
│   ├── goal_tracking.py              # "我的目标完成情况" → 目标汇总
│   ├── skill_gap.py                  # "我离目标还差什么" → gap 分析
│   ├── next_steps.py                 # "我现在该做什么" → 行动建议
│   │
│   ├── # ── 情绪与状态 ──
│   ├── mood_summary.py               # "我最近状态怎么样" → 情绪聚合
│   ├── worries.py                    # "我在焦虑什么" → 反思召回
│   ├── reflection.py                 # "我之前对 X 怎么想的" → 历史反思
│   │
│   └── # ── 对话 ──
│       conversation.py               # "我们上次聊了什么" → 历史对话召回
│
├── extractors/                       # LLM 抽取（独立模块，可测试）
│   ├── __init__.py
│   ├── resume_parser.py              # 简历 → 技能 + 经历 + 项目
│   ├── jd_parser.py                  # JD → 硬性要求 + 加分项 + 岗位类型
│   ├── reference_profiler.py         # 学长帖 → 背景特征 + 成长路径 + 去向
│   ├── project_tagger.py             # 项目描述 → 技术栈 + 成果标签
│   ├── company_profiler.py           # 调研笔记 → 公司特征标签
│   └── summarizer.py                 # 长文本 → 摘要（通用）
│
├── projections/                      # SQLite 派生视图（只读）
│   ├── __init__.py
│   ├── markdown.py                   # → memory.md / skills.md 等（前端展示）
│   └── snapshot.py                   # → Agent 系统提示注入的结构化快照
│
└── cognee_admin/                     # Cognee 运维
    ├── __init__.py
    ├── datasets.py                   # 所有 dataset 名称常量定义
    ├── cognify_loop.py               # 定时 cognify 后台任务
    └── compensate.py                 # 失败摄入的补偿重试
```

---

## 7. Cognee Dataset 分区

Cognee 内部用 dataset 隔离不同内容类型，避免查询时跨类污染。

| Dataset 名称 | 存放内容 |
|---|---|
| `lumen_profile` | 简历文本（仅用户本人档案）|
| `lumen_jd` | JD 文本 |
| `lumen_learning` | 课程笔记、读书笔记、论文笔记、文章 |
| `lumen_projects` | 项目描述、比赛复盘、博客文章 |
| `lumen_job_search` | 面试反馈、Mentor 反馈、推荐信 |
| `lumen_reference` | 学长经验贴、公司调研、行业报告 |
| `lumen_planning` | 职业规划、决策思考原文 |
| `lumen_reflection` | 日记、周记、复盘、随手想法 |
| `lumen_chat` | 对话摘要 |

查询时按场景选 dataset：

```python
# "我适合这个 JD 吗"
semantic.search(jd_text, datasets=["lumen_profile", "lumen_projects"])

# "和我类似的学长去了哪"
semantic.search(profile_query, datasets=["lumen_reference"])

# "我在焦虑什么"
semantic.search(query, datasets=["lumen_reflection", "lumen_chat"])
```

---

## 8. 各层职责

### 8.1 Stores（存储层）

屏蔽底层细节，暴露领域无关的存取 API。

**relational.py**：不关心业务，只关心 CRUD。不做 LLM 调用，不做跨表组合分析。

**semantic.py**：Cognee 的薄封装。统一 dataset 命名、错误处理、重试逻辑。

```python
async def ingest_document(content, doc_id, dataset, metadata=None): ...
async def search(query, datasets, top_k=10): ...
async def get_related(doc_id, datasets): ...
```

**documents.py**：文件落盘 + documents 表元数据维护。

**禁止**：在 stores 里写业务判断、LLM 调用、跨层组合。

---

### 8.2 Ingest（摄入层）

把外部输入按既定流程写入相应存储。每个文件负责一种内容类型。

结构统一：落盘（如有文件）→ 元数据入 SQLite → LLM 抽取结构化（如需要）→ 结构化事件入 SQLite → 全文入 Cognee。

```python
# ingest/project.py
async def ingest_project(user_id, name, description, tech_stack, outcome, source="github"):
    # 1. 写 projects 表（结构化）
    project_id = await relational.write_project(user_id, name, tech_stack, outcome)

    # 2. LLM 提取技术标签和成果类型
    tags = await extractors.project_tagger.tag(description)
    await relational.write_project_tags(project_id, tags)

    # 3. 写 GrowthEvent（技能习得）
    for skill in tags.skills:
        await relational.write_growth_event(user_id, "skill_applied", skill, source=f"project:{project_id}")

    # 4. 描述进 Cognee
    await semantic.ingest_document(
        content=f"{name}\n\n{description}\n\n成果：{outcome}",
        doc_id=f"project:{project_id}",
        dataset="lumen_projects",
    )
```

Ingest 函数只做编排，具体存取逻辑在 stores。

---

### 8.3 Query（查询编排层）

把 Agent 的业务问题翻译成对存储层的具体查询。按问题类型分文件，每个文件处理一类场景。

```python
# query/skill_gap.py
async def analyze_skill_gap(user_id, target_role_or_jd):
    # 1. 用户当前技能（SQLite）
    current_skills = await relational.get_skills(user_id)

    # 2. 目标要求（可能是 JD 文本或岗位名称）
    requirements = await extractors.jd_parser.parse(target_role_or_jd)

    # 3. 用户项目（证明技能的证据）
    evidence = await semantic.search(
        " ".join(requirements.must_have),
        datasets=["lumen_projects", "lumen_profile"],
        top_k=8,
    )

    # 4. gap 分析
    return _compute_gap(current_skills, requirements, evidence)
```

---

### 8.4 Extractors（LLM 抽取层）

把非结构化文本变成结构化数据。独立模块，独立 prompt，可单元测试。

```python
# extractors/reference_profiler.py
class ReferenceProfile(BaseModel):
    school: str
    major: str
    grade_at_graduation: str
    key_experiences: list[str]   # 关键经历节点
    destination: str             # 最终去向
    career_type: str             # 大厂 / 创业 / 读研 / 出海 ...
    turning_points: list[str]    # 关键转折点

async def profile(text: str) -> ReferenceProfile:
    return await llm.structured_call(REFERENCE_PROFILER_PROMPT, ReferenceProfile, text)
```

---

### 8.5 Projections（投影层）

SQLite 数据的只读派生视图。

**snapshot.py**：为 Agent 系统提示生成结构化用户快照。

```python
async def get_user_snapshot(user_id) -> UserSnapshot:
    return UserSnapshot(
        profile=await relational.get_profile(user_id),
        skills=await relational.get_skills(user_id),
        experiences=await relational.get_experiences(user_id),
        projects=await relational.get_projects(user_id),
        recent_goals=await relational.get_active_goals(user_id),
        recent_decisions=await relational.get_recent_decisions(user_id, limit=3),
        learning_records=await relational.get_recent_learning(user_id, limit=5),
    )
```

**markdown.py**：为前端"记忆"页面生成人类可读的 .md 文件。

---

### 8.6 Facade（门面层）

Agent 唯一入口。方法按场景命名，调用方不感知底层。

```python
class LumenMemory:
    # ── 写入：个人档案 ──
    async def record_growth_event(self, user_id, event_type, payload): ...
    async def record_award(self, user_id, name, level, date): ...

    # ── 写入：学习 ──
    async def ingest_course_note(self, user_id, course, content): ...
    async def record_mooc(self, user_id, platform, course, completed_at): ...
    async def ingest_book_note(self, user_id, title, author, content): ...
    async def ingest_paper_note(self, user_id, title, content): ...
    async def ingest_article(self, user_id, url, title, content, annotation): ...

    # ── 写入：项目与创作 ──
    async def ingest_project(self, user_id, name, description, tech_stack, outcome): ...
    async def ingest_paper(self, user_id, title, file_bytes): ...
    async def ingest_blog_post(self, user_id, title, content, url=None): ...

    # ── 写入：求职 ──
    async def ingest_resume(self, user_id, file_bytes): ...
    async def ingest_jd(self, user_id, text, company=None, role=None): ...
    async def record_application(self, user_id, company, role, jd_id=None): ...
    async def record_interview(self, user_id, company, role, feedback, outcome): ...
    async def record_offer(self, user_id, company, role, package, accepted): ...

    # ── 写入：人脉 ──
    async def ingest_mentor_feedback(self, user_id, mentor, content): ...
    async def ingest_alumni_conversation(self, user_id, alumni_bg, content): ...

    # ── 写入：外部参考 ──
    async def ingest_reference_post(self, user_id, content, source_bg=None): ...
    async def ingest_company_research(self, user_id, company, content): ...
    async def ingest_industry_report(self, user_id, title, file_bytes): ...
    async def record_salary_data(self, user_id, company, role, package): ...

    # ── 写入：规划与决策 ──
    async def ingest_career_plan(self, user_id, content): ...
    async def record_goal(self, user_id, content, target_date=None): ...
    async def record_decision(self, user_id, choice, context, rationale): ...

    # ── 写入：情绪与反思 ──
    async def ingest_journal(self, user_id, content, date=None): ...
    async def ingest_retrospective(self, user_id, content, subject=None): ...
    async def ingest_quick_note(self, user_id, content): ...

    # ── 写入：对话 ──
    async def archive_conversation(self, user_id, conversation_id): ...

    # ────────────────────────────────────────────────

    # ── 读取：关于自己 ──
    async def get_profile_snapshot(self, user_id): ...
    async def get_skills(self, user_id, domain=None): ...
    async def get_experiences(self, user_id, time_range=None): ...
    async def get_achievements(self, user_id): ...
    async def get_growth_narrative(self, user_id, time_range=None): ...

    # ── 读取：求职 ──
    async def analyze_jd_match(self, user_id, jd_text): ...
    async def get_application_status(self, user_id): ...
    async def get_interview_review(self, user_id): ...

    # ── 读取：外部参考 ──
    async def find_similar_paths(self, user_id, top_k=5): ...
    async def get_company_intel(self, user_id, company): ...
    async def get_market_trends(self, domain=None): ...

    # ── 读取：学习与规划 ──
    async def get_learning_progress(self, user_id, recent_days=30): ...
    async def get_goal_status(self, user_id): ...
    async def analyze_skill_gap(self, user_id, target_role): ...
    async def suggest_next_steps(self, user_id): ...

    # ── 读取：情绪与状态 ──
    async def get_mood_summary(self, user_id, recent_days=14): ...
    async def recall_worries(self, user_id): ...
    async def recall_reflection(self, user_id, topic): ...

    # ── 读取：对话 ──
    async def recall_conversation(self, user_id, query): ...
```

---

## 9. 主要数据流

### 9.1 写入：用户输入一个 GitHub 项目

```
facade.ingest_project(user_id, name, description, tech_stack, outcome)
    ↓
ingest/project.py
    ├─ relational.write_project()          → SQLite projects 表
    ├─ extractors.project_tagger.tag()     → 技术标签 + 成果类型
    ├─ relational.write_growth_event()     → GrowthEvent: skill_applied
    └─ semantic.ingest_document()          → Cognee: lumen_projects
```

### 9.2 写入：用户贴了一篇学长经验帖

```
facade.ingest_reference_post(user_id, content, source_bg="清华CS->Google")
    ↓
ingest/reference_post.py
    ├─ documents.save_file()               → 文本落盘（可选）
    ├─ extractors.reference_profiler()     → 背景特征 + 路径节点 + 去向
    ├─ relational.write_reference_meta()   → SQLite references 表（结构化部分）
    └─ semantic.ingest_document()          → Cognee: lumen_reference
```

### 9.3 写入：用户写了一篇日记

```
facade.ingest_journal(user_id, content)
    ↓
ingest/journal.py
    ├─ relational.write_journal_meta()     → SQLite journals 表（日期、关键词）
    └─ semantic.ingest_document()          → Cognee: lumen_reflection
```

### 9.4 查询："我离进大厂还差什么"

```
facade.analyze_skill_gap(user_id, "大厂后端工程师")
    ↓
query/skill_gap.py
    ├─ relational.get_skills()             → 当前技能列表
    ├─ relational.get_projects()           → 项目证据
    ├─ semantic.search(                    → 相关经验贴 + 公司调研
         "后端工程师岗位要求",
         datasets=["lumen_reference", "lumen_jd"]
       )
    └─ _compute_gap()                      → 缺口列表 + 建议优先级
```

### 9.5 查询："和我背景类似的人最后去了哪"

```
facade.find_similar_paths(user_id)
    ↓
query/similar_paths.py
    ├─ projections.snapshot.get_user_snapshot()   → 用户背景文本
    ├─ semantic.search(
         user_background_text,
         datasets=["lumen_reference"],
         top_k=10
       )                                           → 最相似的学长经验贴
    ├─ relational.get_reference_meta(doc_ids)      → 结构化去向数据
    └─ _aggregate_destinations()                   → 去向分布
```

### 9.6 查询："我最近在焦虑什么"

```
facade.recall_worries(user_id)
    ↓
query/worries.py
    ├─ semantic.search(
         "焦虑 担心 纠结 压力",
         datasets=["lumen_reflection", "lumen_chat"],
         top_k=5
       )                                           → 最相关的日记/反思
    └─ 直接返回原文段落给 Agent 推理
```

### 9.7 对话开始时（每轮必做）

```
每次对话开始
    ↓
projections.snapshot.get_user_snapshot(user_id)
    ↓
注入 Agent 系统提示：
    [用户画像]
    学校：XX | 专业：XX | 年级：XX

    [近期目标]
    ...

    [技能]
    Python（熟练）、SQL（掌握）...

    [最近在做]
    参加了 XX 比赛、在学 XX 课程...
```

---

## 10. 关键设计决策

### 10.1 GrowthEvent 不进 Cognee

已结构化的事实（技能、经历）进 Cognee 等于把结构化数据转成自然语言再重建结构，损失信息、增加成本。SQL 处理精确查询天然更好。

### 10.2 情绪与反思类内容只进 Cognee

日记、随手想法、复盘这类内容元数据价值低，主要靠语义召回。SQLite 只存最少的元信息（日期、字数），正文全进 Cognee。

### 10.3 文件类内容三层都进

有原始文件（简历、论文、报告）的内容：文件系统存原档、SQLite 存元数据和抽取的结构化字段、Cognee 存语义可检索的文本。删除时三层一起清理。

### 10.4 Extractors 独立成层

LLM 抽取 prompt 需要持续迭代。独立后可单元测试，不同内容类型可用不同模型，抽取结果可缓存。嵌在 ingest 里会导致"改 prompt 要碰摄入流程"。

### 10.5 Dataset 分区而非全量搜索

不同内容类型用不同 Cognee dataset，查询时按场景指定。避免"问面试经验"时返回日记内容。

### 10.6 Agent 通过 Snapshot 了解用户，而非运行时检索

每轮对话开始时注入结构化快照到系统提示，Agent 的基本认知来自这里，而不是每次都去搜索记忆。按需语义召回只用于补充细节。

### 10.7 Cognify 异步批量，不在写入路径上

`cognify()` 调 LLM 贵且慢。每次 ingest 后标记待 cognify，由后台 loop 每 60 秒批量触发。对个人助手场景，60 秒延迟完全可接受。

### 10.8 错误处理：降级而非失败

Cognee 写入失败 → SQLite 已成功 → 标记待补偿，后续重试。  
Cognee 查询失败 → 回退到 SQLite FTS5 → 能力降级但功能不挂。

---

## 11. 反模式（明确禁止）

**不要把结构化数据喂给 Cognee**
```python
# 错误
await cognee.remember("用户掌握了 Python（熟练）")
```

**不要在 Stores 层写业务逻辑**
```python
# 错误
class RelationalStore:
    async def get_skills_for_jd_match(self, user_id, jd): ...
# 这是 Query 层的事
```

**不要让 Agent 直接调 Stores**
```python
# 错误
skills = await relational.get_skills(user_id)  # Agent 不该知道 relational
```

**不要一个 ingest 函数处理多种输入类型**
```python
# 错误
async def ingest(content, type):
    if type == "resume": ...
    elif type == "journal": ...
```

**不要把 Extractors 嵌在 Ingest 里**
```python
# 错误
async def ingest_resume(file_bytes):
    PROMPT = "从简历中提取..."  # 这个 prompt 放在这里无法单独测试
```

**不要让 Query 层引入新的存储**  
Query 只编排已有 stores 的数据，不能在 query 函数里突然建一个缓存或新表。

---

## 12. SQLite 表清单

```sql
-- 原有
growth_events(id, user_id, event_type, entity_type, entity_id, payload_json, ...)

-- 文档管理
documents(id, user_id, type, file_path, title, source, ingested_at, ...)

-- 学习
learning_records(id, user_id, platform, course, completed_at, ...)
book_notes_meta(id, user_id, title, author, note_doc_id, created_at)
paper_notes_meta(id, user_id, title, authors, note_doc_id, created_at)
article_bookmarks(id, user_id, url, title, doc_id, annotation, saved_at)
learning_summaries(id, user_id, period, doc_id, created_at)

-- 项目与创作
projects(id, user_id, name, source, tech_stack_json, outcome, doc_id, created_at)
project_tags(project_id, tag, tag_type)  -- tag_type: skill/domain/outcome
competitions(id, user_id, name, result, doc_id, held_at)

-- 求职
applications(id, user_id, company, role, jd_doc_id, status, applied_at)
interviews(id, user_id, application_id, round, outcome, feedback_doc_id, interviewed_at)
offers(id, user_id, application_id, package_json, accepted, decided_at)

-- 人脉
contacts(id, user_id, name, relation, background)
mentor_feedbacks(id, user_id, contact_id, doc_id, created_at)
alumni_conversations(id, user_id, alumni_bg, doc_id, created_at)

-- 外部参考
references_meta(id, user_id, source_bg, destination, career_type, doc_id, created_at)
companies(id, name, industry, tags_json)
company_research(id, user_id, company_id, doc_id, created_at)
salary_data(id, user_id, company, role, package_json, offer_date)

-- 规划与决策
goals(id, user_id, content, target_date, status, created_at)
decisions(id, user_id, choice, rationale_doc_id, made_at)
career_plans(id, user_id, doc_id, version, created_at)

-- 情绪与反思
journals(id, user_id, date, word_count, doc_id, created_at)
retrospectives(id, user_id, subject, doc_id, created_at)

-- 荣誉
awards(id, user_id, name, level, date, description)
```

---

## 13. 附录

### A. 模块依赖方向（单向）

```
facade.py
   ↓
query/      →  projections/  →  stores/relational
   ↓
ingest/     →  extractors/   →  stores/{relational, semantic, documents}
   ↓
cognee_admin/              →  stores/semantic
```

上层依赖下层，下层不知道上层，stores 之间互不依赖。

### B. 测试策略

| 层 | 测试方式 |
|---|---|
| stores | 集成测试（真实 SQLite + Cognee 临时实例）|
| extractors | 单元测试（固定输入 → 断言输出结构）|
| ingest | 集成测试（mock 文件，验证落库）|
| query | 集成测试（预置数据，验证编排结果）|
| projections | 单元测试（给 SQLite 数据 → 断言快照格式）|
| facade | 端到端测试 |

### C. 扩展新内容类型的步骤

1. 在 `stores/relational.py` 加对应表的 CRUD 方法
2. 在 `ingest/` 新建一个文件，定义摄入流程
3. 在 `extractors/` 新建抽取器（如需要）
4. 在 `query/` 新建对应的查询编排
5. 在 `facade.py` 暴露写入方法和读取方法
6. 在 `cognee_admin/datasets.py` 注册新的 dataset 名称（如需要）

每步互相独立，新增内容类型不影响现有功能。

### D. 与当前代码的映射

| 当前文件 | 新架构位置 |
|---|---|
| `services/cognee_service.py` | `stores/semantic.py` |
| `services/cognee_projector.py` | 拆入各 `ingest/*.py` |
| `services/lumen_memory.py` | 拆成 `facade.py` + `query/*.py` |
| `services/md_projector.py` | `projections/markdown.py` |
| `agent/cognee_client.py` | `cognee_admin/` 下 |
| `routers/memory.py` | 不变，调用 `facade.py` |
