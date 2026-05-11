---
name: gstack-guide
description: |
  Guide for using Garry Tan's gstack — the 92K+ star AI engineering workflow framework.
  Use this skill whenever the user mentions gstack, asks how to use gstack skills,
  wants to install gstack, needs help with gstack commands like /office-hours /plan-ceo-review
  /ship /qa /review /browse /cso /autoplan, or is trying to set up Garry Tan's Claude Code
  workflow. Also trigger when the user wants to improve their AI-assisted development
  workflow, set up structured code review/QA/shipping with AI, or asks about YC-style
  product development with AI agents. Covers installation, all 23+ skills, sprint workflow,
  and best practices.
---

# gstack 使用指南

Garry Tan（Y Combinator CEO）开源的 AI 工程工作流框架，将 Claude Code 变成一支虚拟工程团队。

## 什么是 gstack

gstack 是一套 **23+ 个结构化 skill**，覆盖完整的软件开发生命周期：

- **思考** → **规划** → **构建** → **审查** → **测试** → **发布** → **复盘**
- 把 Claude Code 变成虚拟团队：CEO、设计师、工程经理、安全官、QA、发布工程师
- 92K+ GitHub stars，MIT 协议，免费开源
- 支持 Claude Code、Codex、Cursor、OpenCode 等 8+ 个 AI 代理平台

## 安装（30 秒）

### 个人安装

在 Claude Code 中粘贴：

```bash
git clone --single-branch --depth 1 https://github.com/garrytan/gstack.git ~/.claude/skills/gstack && cd ~/.claude/skills/gstack && ./setup
```

然后让 Claude 在 `CLAUDE.md` 中添加 gstack 章节，列出所有可用技能。

### 团队模式（推荐）

```bash
# 安装团队模式
cd ~/.claude/skills/gstack && ./setup --team

# 在当前项目初始化（只需做一次）
~/.claude/skills/gstack/bin/gstack-team-init required

git add .claude/ CLAUDE.md && git commit -m "require gstack for AI-assisted work"
```

团队成员安装 gstack 后，每次启动 Claude Code 会自动同步最新版本。

### 其他 AI 代理

```bash
# 支持 Codex、Cursor、OpenCode 等
git clone --single-branch --depth 1 https://github.com/garrytan/gstack.git ~/gstack
cd ~/gstack && ./setup --host codex    # 或 cursor / opencode / factory 等
```

---

## 核心工作流

gstack 是一个**流程**，不是工具集合。技能按 sprint 顺序运行：

```
/office-hours → /plan-ceo-review → /plan-eng-review → [实现] → /review → /qa → /ship → /retro
```

每个技能的输出会自动流入下一个技能。例如 `/office-hours` 写的设计文档会被 `/plan-ceo-review` 读取。

---

## 技能分类速查

### 1. 思考与规划（Think & Plan）

| 技能 | 角色 | 用途 |
|------|------|------|
| `/office-hours` | YC 顾问 | **必须从这里开始**。用 6 个 forcing questions 重新定义产品，挑战你的前提假设 |
| `/plan-ceo-review` | CEO/创始人 | 4 种模式审视方案：扩大范围、选择性扩展、保持范围、缩减范围 |
| `/plan-eng-review` | 工程经理 | 锁定架构、数据流、边界情况、测试矩阵 |
| `/plan-design-review` | 高级设计师 | 给每个设计维度打分 0-10，指出 AI slop |
| `/plan-devex-review` | DX 负责人 | 审计开发者体验，对标竞争对手 |
| `/autoplan` | 审查流水线 | **自动链式运行** CEO + 设计 + 工程审查 |

### 2. 构建（Build）

| 技能 | 角色 | 用途 |
|------|------|------|
| `/design-consultation` | 设计合伙人 | 从零创建完整设计系统 |
| `/design-shotgun` | 设计探索者 | 生成 4-6 个设计变体，浏览器中对比选择 |
| `/design-html` | 设计工程师 | 将设计稿变成生产级 HTML（30KB，零依赖） |
| `/investigate` | 调试专家 | 系统性根因调试，铁律：没有调查就没有修复 |

### 3. 审查（Review）

| 技能 | 角色 | 用途 |
|------|------|------|
| `/review` | 资深工程师 | **PR 预审**。自动修复明显问题，标记生产级 bug |
| `/design-review` | 会写代码的设计师 | 审计实时代码中的设计问题，原子提交修复 |
| `/devex-review` | DX 测试员 | 实际测试 onboarding 流程，对比计划 vs 现实 |
| `/codex` | 外部评审 | 用 Codex 做独立 diff 审查 |

### 4. 测试（Test）

| 技能 | 角色 | 用途 |
|------|------|------|
| `/qa` | QA 负责人 | **测试+修复**。打开真实浏览器，点击，截图，找 bug，修复，回归测试 |
| `/qa-only` | QA 报告员 | 只报告 bug，不修改代码 |
| `/browse` | QA 工程师 | 给 AI 眼睛。真实 Chromium 浏览器，~100ms/命令 |
| `/setup-browser-cookies` | 会话管理 | 从真实浏览器导入 cookie，测试登录后的页面 |
| `/benchmark` | 性能工程师 | 基线页面加载时间、Core Web Vitals |

### 5. 发布（Ship）

| 技能 | 角色 | 用途 |
|------|------|------|
| `/ship` | 发布工程师 | 同步 main、运行测试、审查覆盖率、推送、开 PR |
| `/land-and-deploy` | 发布工程师 | 合并 PR、等待 CI、验证生产环境健康 |
| `/canary` | SRE | 部署后监控：console 错误、性能回退、页面失败 |
| `/document-release` | 技术写作 | 更新所有项目文档，匹配刚发布的代码 |

### 6. 系统健康（Health）

| 技能 | 角色 | 用途 |
|------|------|------|
| `/cso` | 安全官 | OWASP Top 10 + STRIDE 威胁模型，8/10+ 置信度门槛 |
| `/retro` | 工程经理 | 团队感知周回顾，个人贡献分解 |
| `/learn` | 知识管理 | 管理项目学习，搜索、修剪、导出 |
| `/careful` | 安全护栏 | rm -rf、DROP TABLE 等危险操作前警告 |
| `/freeze` / `/unfreeze` | 范围控制 | 限制编辑到特定目录 / 解除限制 |
| `/guard` | 完全安全模式 | careful + freeze 的组合 |

### 7. 工具与设置（Tools）

| 技能 | 用途 |
|------|------|
| `/setup-deploy` | 配置部署设置 |
| `/setup-gbrain` | 设置 gbrain 知识库 |
| `/sync-gbrain` | 同步代码到 gbrain |
| `/pair-agent` | 与其他 AI 代理共享浏览器 |
| `/gstack-upgrade` | 升级 gstack |
| `/open-gstack-browser` | 启动带侧边栏的 GStack 浏览器 |

---

## 典型使用场景

### 场景 1：从零开发新功能

```
用户：我想做一个每日简报应用

/office-hours          → 重新定义产品（可能是"个人 AI 幕僚"）
/plan-ceo-review       → 审查产品方案
/plan-eng-review       → 锁定架构
[实现代码]
/review                → 代码审查
/qa https://staging... → 浏览器测试
/ship                  → 发布 PR
```

### 场景 2：快速迭代现有功能

```
[修改代码]
/review                → 自动修复 + 标记问题
/qa                    → 测试并修复 bug
/ship                  → 发布
```

### 场景 3：安全审计

```
/cso                   → 全面安全扫描
```

### 场景 4：设计探索

```
/design-shotgun        → 生成多个设计变体
[选择喜欢的]
/design-html           → 变成生产代码
```

---

## 最佳实践

1. **永远从 `/office-hours` 开始** — 不要直接写代码，先重新定义问题
2. **使用 `/autoplan` 节省时间** — 自动运行 CEO + 设计 + 工程审查
3. **每次代码变更后运行 `/review`** — 在合并前发现生产级 bug
4. **用 `/qa` 而不是自己手动测试** — 真实浏览器，系统性测试
5. **团队模式保持同步** — 用 `./setup --team` 避免版本漂移
6. **描述要具体** — 给 AI 的具体任务比模糊指令效果好 10 倍
7. **不要跳过审查环节** — gstack 的价值在于结构化流程，不是单个工具

---

## 故障排除

**Claude 说看不到技能？**
- 确保 `CLAUDE.md` 中有 gstack 章节
- 检查 `~/.claude/skills/gstack/` 是否存在

**技能没有触发？**
- 确保描述（description）中包含触发关键词
- 尝试直接输入 `/skill-name`

**浏览器技能不工作？**
- 确保已安装 Bun 和 Node.js
- 运行 `/setup-browser-cookies` 导入登录状态

---

## 了解更多

- GitHub: https://github.com/garrytan/gstack
- 官方文档: 仓库内的 `docs/` 目录
- 社区指南: https://www.littlemight.com/garry-tan-gstack-definitive-guide/

---

*本 skill 帮助你快速上手 gstack。如需了解某个具体技能的详细用法，请直接运行该技能或查阅其 SKILL.md。*
