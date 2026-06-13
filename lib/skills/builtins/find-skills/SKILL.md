---
name: find-skills
description: 帮助用户从 skills.sh 开放生态中发现和安装 Agent 技能。当用户问"有没有做 X 的技能""找找 X 的技能""skills.sh 上有什么"或想扩展能力时触发。
---

# Find Skills

帮助用户从 [skills.sh](https://www.skills.sh/) 开放 Agent 技能生态中发现技能。

## 何时触发

- 用户说"有没有做 X 的技能"、"找找 X 的技能"
- 用户问"skills.sh 上有什么"、"有什么好用的技能"
- 用户想扩展我的能力
- 用户问能不能做某个特定领域的事（设计、测试、部署等）

## 工作流程

### 1. 理解需求
确定用户需要的领域和具体任务。

### 2. 搜索 skills.sh

用 `web_search` 搜索 skills.sh 排行榜或按关键词搜索：

```text
web_search(query="site:skills.sh <关键词>")
```

或直接查看排行榜：

```text
web_extract(url="https://www.skills.sh/leaderboard")
```

按关键词搜索特定技能：

```text
web_extract(url="https://www.skills.sh/search?q=<关键词>")
```

### 3. 查看技能详情

找到候选后，用 `web_extract` 查看具体技能页面：

```text
web_extract(url="https://www.skills.sh/<作者>/<仓库>/<技能名>")
```

重点关注：
- 技能描述和用途
- 安装量（优先 1K+）
- 来源信誉（vercel-labs、anthropics、microsoft 等官方源更可信）
- GitHub 星数

### 4. 推荐给用户

呈现格式：
- 技能名称和用途
- 安装量和来源
- 安装命令示例：`npx skills add <源> --skill <技能名>`
- skills.sh 链接

### 5. 安装（如果用户同意）

用 shell 执行：

```bash
npx skills add <GitHub仓库> --skill <技能名>
```

> 注意：skills.sh 生态是为 Claude Code / Cursor 等 Agent 平台设计的，安装后不一定能直接适配到 Lumen 的 skill 系统。安装后可能需要手动改造。

## 常用技能类别

| 类别 | 搜索关键词 |
|------|-----------|
| 浏览器自动化 | browser automation, web scrape |
| 测试 | testing, e2e, playwright |
| 部署 | deploy, devops, docker |
| 文档 | docs, readme |
| 代码质量 | review, lint, refactor |
| 设计 | ui, ux, design system |
| 搜索 | search, web search |

## 未找到时

1. 告知暂无现成技能
2. 用现有能力直接帮用户做
3. 建议用户自己创建一个 skill
