"""RAG 模块：文档导入 + 检索。"""

from .service import import_document, retrieve_for_query

__all__ = ["import_document", "retrieve_for_query"]
