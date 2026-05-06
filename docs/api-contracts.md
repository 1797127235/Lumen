# CareerOS API 契约

**项目**: CareerOS（码路领航）
**Base URL**: `http://localhost:3000` (Docker) / `http://localhost:5173` (开发)
**最后更新**: 2026-05-06

---

## 概述

CareerOS 提供 RESTful API，所有端点以 `/api` 为前缀。对话接口使用 SSE (Server-Sent Events) 实现流式输出。

---

## 1. Health API

### GET /api/health

健康检查端点。

**响应**:
```json
{
  "status": "ok",
  "cognee_status": "ready" | "not_installed" | "error"
}
```

---

## 2. Chat API

### POST /api/chat

SSE 流式对话端点。

**请求体**:
```json
{
  "message": "我想做 AI Agent",
  "conversation_id": "optional-uuid",
  "user_id": "demo_user"
}
```

**SSE 事件流**:
```
data: {"type": "token", "content": "你好", "conversation_id": "uuid"}
data: {"type": "token", "content": "！我是", "conversation_id": "uuid"}
data: {"type": "done", "conversation_id": "uuid"}
```

**错误事件**:
```
data: {"type": "error", "message": "API Key 未配置"}
```

### GET /api/chat/history

获取对话历史列表。

**查询参数**:
- `user_id` (string, 默认 "demo_user")
- `limit` (int, 默认 20)

**响应**:
```json
[
  {
    "conversation_id": "uuid",
    "title": "对话标题",
    "message_count": 10,
    "last_message_at": "2026-05-06T12:00:00Z",
    "created_at": "2026-05-06T10:00:00Z"
  }
]
```

### GET /api/chat/{conversation_id}

获取单条对话的所有消息。

**响应**:
```json
[
  {
    "message_id": "uuid",
    "role": "user" | "assistant",
    "content": "消息内容",
    "created_at": "2026-05-06T12:00:00Z"
  }
]
```

### DELETE /api/chat/{conversation_id}

删除对话及其消息。

**响应**: 204 No Content

---

## 3. Profile API

### GET /api/profile/me

获取用户画像。

**查询参数**:
- `user_id` (string, 默认 "demo_user")

**响应**:
```json
{
  "nickname": "用户昵称",
  "school_name": "学校名称",
  "school_level": "985" | "211" | "double_first_class" | "normal",
  "major": "专业",
  "grade": "junior",
  "graduation_year": 2026,
  "target_direction": "AI",
  "target_company_level": "top" | "major" | "medium" | "state_owned",
  "current_skills": [
    {"name": "Python", "level": "familiar", "context": ""}
  ],
  "gpa": "3.8",
  "ranking": "前 10%",
  "awards": ["奖项1"],
  "bio": "个人简介",
  "city": "北京",
  "english_level": "CET-6",
  "expected_salary": "20-30K",
  "projects": [...],
  "work_experience": [...]
}
```

### PATCH /api/profile/me

局部更新用户画像。

**请求体**: 同 GET 响应，只传需要更新的字段。

**响应**: 更新后的完整画像。

### DELETE /api/profile/me

重置用户画像（保留 nickname）。

**响应**: 204 No Content

### POST /api/profile/resume

上传简历，LLM 自动提取画像。

**请求**: `multipart/form-data`
- `file`: PDF/DOCX/TXT 文件

**响应**:
```json
{
  "success": true,
  "message": "简历解析成功",
  "preview": "简历预览文本...",
  "content_length": 1234
}
```

---

## 4. Memory API

### GET /api/memory/me

读取用户 `.md` 画像内容。`.md` 为空时会尝试从数据库事件重建。

**查询参数**:
- `user_id` (string, 默认 "demo_user")

**响应**:
```json
{
  "content": "# 用户核心记忆\n\n## 基础信息\n..."
}
```

### GET /api/memory/stats

获取记忆统计。

**查询参数**:
- `user_id` (string, 默认 "demo_user")

**响应**:
```json
{
  "status": "ready" | "not_installed" | "error",
  "count": 42
}
```

### GET /api/memory/list

获取所有记忆条目。

**响应**:
```json
[
  {
    "id": "event-uuid",
    "memory": "事件内容或 payload",
    "created_at": "2026-05-06T12:00:00Z",
    "categories": ["profile_updated"]
  }
]
```

### POST /api/memory/reset

重置记忆（SQLite + .md + Cognee）。

**响应**:
```json
{
  "deleted": 42
}
```

### POST /api/memory/rebuild

从 SQLite 重建 .md 和 Cognee 索引。

**响应**:
```json
{
  "message": "重建成功",
  "user_id": "demo_user",
  "md_success": true,
  "cognee_success": true
}
```

### DELETE /api/memory/{event_id}

删除单条事件记忆，并重新投影 `.md`。

**查询参数**:
- `user_id` (string, 默认 "demo_user")

**响应**:
```json
{
  "deleted": "event-uuid"
}
```

**注意**: 因 SQLite 3.45.3 FTS5 触发器兼容问题，删除会先清理触发器 → 执行 DELETE → 重建 FTS 虚拟表 → 重建触发器。低版本不受影响。

### GET /api/memory/search

搜索记忆（FTS5 全文搜索）。

**查询参数**:
- `user_id` (string)
- `query` (string, 必填)
- `limit` (int, 默认 10)

**响应**: 同 /api/memory/list

---

## 5. Skills API

### GET /api/skills

获取用户所有技能记录。

**查询参数**:
- `user_id` (string, 默认 "demo_user")

**响应**:
```json
[
  {
    "id": "skill-uuid",
    "skill_name": "Python",
    "skill_level": "familiar",
    "context": "项目经验",
    "created_at": "2026-05-06T12:00:00Z"
  }
]
```

### POST /api/skills

创建技能记录。

**请求体**:
```json
{
  "skill_name": "React",
  "skill_level": "intermediate",
  "context": "3 个项目经验"
}
```

### PATCH /api/skills/{skill_id}

更新技能记录。

### DELETE /api/skills/{skill_id}

删除技能记录。

---

## 6. Config API

### GET /api/config

获取当前配置。

**响应**:
```json
{
  "llm_provider": "dashscope",
  "llm_model": "qwen-plus",
  "llm_api_key": "",
  "llm_base_url": "",
  "has_llm_key": true,
  "embedding_provider": "dashscope",
  "embedding_model": "text-embedding-v3",
  "embedding_api_key": "",
  "embedding_base_url": "",
  "has_embedding_key": true
}
```

### POST /api/config

更新配置。

**请求体**: 同 GET 响应。

### POST /api/config/test

测试 LLM 连接。

**响应**:
```json
{
  "ok": true,
  "latency_ms": 234,
  "error": ""
}
```

---

## 错误响应

所有端点在出错时返回：

```json
{
  "detail": "错误描述"
}
```

常见状态码:
- `400`: 请求参数错误
- `404`: 资源不存在
- `422`: 请求体验证失败
- `500`: 服务器内部错误
- `502`: LLM 调用失败
- `503`: 服务不可用（如 Cognee 未就绪）

---

## 认证

当前无认证。`user_id` 由客户端 localStorage 控制。

**生产环境建议**: 添加 JWT Bearer Token。
