# Knowledge

<p align="center">
  <b>独立知识库 MCP 服务</b><br>
  <i>导入 · 分块 · 向量检索</i>
</p>

---

## 这是什么

Knowledge 是一个基于 RAG 的知识库服务，通过 MCP 协议暴露工具，供 Lumen 或其他 MCP 客户端调用。

- **文档导入**：支持 PDF / TXT / MD / DOCX / HTML
- **语义检索**：基于 Embedding 的向量搜索
- **完全解耦**：独立进程，不依赖 Lumen 代码

**技术栈**：Python 3.11+ + MCP SDK + SQLite + httpx

---

## 快速开始

### 前提

- **Python**（3.11+）
- **Embedding 服务**（OpenAI 兼容 API）

### 启动

```bash
# 安装依赖
pip install -r ../../requirements.txt

# 配置 Embedding
export EMBEDDING_API_KEY=your-key
export EMBEDDING_BASE_URL=https://api.example.com/v1
export EMBEDDING_MODEL=text-embedding-v3

# 启动服务
cd apps/knowledge
python server.py
```

服务默认监听 `http://127.0.0.1:8766/sse`。

### Lumen 接入

编辑 `~/.lumen/config.json`：

```json
{
  "mcp_servers": [
    {
      "name": "knowledge",
      "transport": "sse",
      "url": "http://127.0.0.1:8766/sse"
    }
  ]
}
```

---

## MCP 工具

| 工具 | 说明 |
|------|------|
| `kb_search` | 语义搜索，返回相关文档片段 |
| `kb_import` | 导入文件 |
| `kb_list_documents` | 列出所有已导入文档 |
| `kb_get_documents_meta` | 批量获取文档元信息 |
| `kb_read_chunks` | 读取指定文档分块原文 |
| `kb_delete_document` | 删除指定文档 |
| `kb_stats` | 获取知识库统计信息 |

---

## 架构

```
┌─────────────────────────────────────────────────┐
│  Lumen / 其他 MCP 客户端                         │
└────────────────────┬────────────────────────────┘
                     │ MCP (SSE)
                     ↓
┌─────────────────────────────────────────────────┐
│  Knowledge MCP Server  (127.0.0.1:8766)          │
│                                                  │
│  ┌──────────────┐  ┌──────────────┐              │
│  │  Extractors   │  │  Chunker     │              │
│  │  PDF/DOCX/... │  │  滑动窗口    │              │
│  └──────┬───────┘  └──────┬───────┘              │
│         └────────┬────────┘                      │
│                  ↓                               │
│  ┌──────────────────────────────┐                │
│  │  Embedding → SQLite Storage  │                │
│  │  向量检索 + 文档/分块 CRUD    │                │
│  └──────────────────────────────┘                │
└─────────────────────────────────────────────────┘
```

---

## 项目结构

```
apps/knowledge/
├── server.py           MCP 服务器入口
├── config.py           配置定义
├── embeddings.py       Embedding 客户端
├── storage/
│   ├── connection.py   SQLite 连接 + 迁移
│   ├── kb_store.py     文档/分块 CRUD
│   └── vector_store.py 向量存储 + 余弦检索
└── rag/
    ├── service.py      导入流水线 + 检索入口
    ├── chunker.py      滑动窗口分块
    ├── retrieval.py    HyDE/MQE 扩展检索
    └── extractors/     文档解析器
        ├── txt.py
        ├── pdf.py
        ├── docx.py
        └── html.py
```

---

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `KNOWLEDGE_HOST` | `127.0.0.1` | 监听地址 |
| `KNOWLEDGE_PORT` | `8766` | 监听端口 |
| `KNOWLEDGE_DB_PATH` | `~/.lumen/knowledge.db` | 数据库路径 |
| `EMBEDDING_API_KEY` | - | Embedding API Key |
| `EMBEDDING_BASE_URL` | - | Embedding API Base URL |
| `EMBEDDING_MODEL` | - | Embedding 模型名称 |

---

## License

[MIT](../../LICENSE)
