# 简历上传到画像层

## 后端

### `backend/services/memory.py` — 加一条上传路由

```
POST /api/memory/upload-resume  (multipart: file, user_id)
```

流程：
1. 接收文件 → `parsers.parse_file()` 转 markdown（已有）
2. 截断到 5000 字
3. 用 `openai.AsyncOpenAI` 直接调 LLM，system prompt 让 LLM 返回 JSON：

```json
{
  "profile": {"school_name": "...", "major": "...", "grade": "...", "target_direction": "..."},
  "skills": [{"name": "Python", "level": "advanced", "context": "3年经验"}],
  "experiences": [{"title": "XX实习", "description": "负责..."}]
}
```

4. 解析 JSON → `memory.remember()` 写入 profile_updated / skill_added / experience_added 事件
5. `memory.sync_projections()` → memory.md / skills.md / experiences.md
6. 返回 `{"ok": true, "events": 5}`

**不用的**：PydanticAI Agent、PydanticAI tools、LumenDeps

## 前端

### `src/lib/api.ts` — 加 `uploadResume` 函数

```ts
export async function uploadResume(file: File, userId: string) {
  const form = new FormData()
  form.append('file', file)
  form.append('user_id', userId)
  const res = await fetch('/api/memory/upload-resume', { method: 'POST', body: form })
  return res.json()
}
```

### `src/pages/Profile.tsx` — 画像页加上传按钮

在 "画像" 标题下方加一个文件上传区域：
- 点击或拖拽上传简历（pdf/docx/txt 等）
- 上传中显示 loading
- 完成后调 `getMemoryContent()` 刷新画像
- 失败显示 toast

## 改动汇总

| 文件 | 改动 |
|------|------|
| `backend/services/memory.py` | 加 `upload-resume` 路由 |
| `src/lib/api.ts` | 加 `uploadResume()` |
| `src/pages/Profile.tsx` | 加上传按钮 |
| `backend/services/knowledge.py` | 不动 |
| `src/pages/Knowledge.tsx` | 不动 |
