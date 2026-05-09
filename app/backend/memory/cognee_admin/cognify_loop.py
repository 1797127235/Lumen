"""Cognee 1.0.5 初始化 + 后台 cognify 循环。
从 agent/cognee_client.py 迁移而来。"""

from __future__ import annotations

import logging
import os

from app.backend.config import USER_DATA_DIR

logger = logging.getLogger(__name__)

_cognee_status: str = "not_initialized"
_needs_cognify: bool = False
COGNEE_COGNIFY_INTERVAL_SEC = int(os.environ.get("COGNEE_COGNIFY_INTERVAL_SEC", "60"))


def get_cognee_status() -> str:
    return _cognee_status


def mark_needs_cognify() -> None:
    """add() 后调用，标记需要 cognify。"""
    global _needs_cognify
    _needs_cognify = True


def init_cognee() -> str:
    """初始化 Cognee：路径 + LLM + Embedding 配置。

    所有值来自 get_settings()，即用户在前端设置页的配置。
    """
    global _cognee_status

    os.environ["ENABLE_BACKEND_ACCESS_CONTROL"] = "false"

    try:
        import cognee as _cognee

        from app.backend.config import get_settings

        settings = get_settings()

        # 数据目录
        _cognee.config.data_root_directory(str(USER_DATA_DIR))

        # LLM 配置（用 openai provider，兼容所有 OpenAI 兼容接口）
        llm_api_key = settings.llm_api_key or settings.dashscope_api_key
        llm_base_url = settings.llm_base_url
        if not llm_base_url:
            if settings.llm_provider == "dashscope":
                llm_base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
            elif settings.llm_provider == "deepseek":
                llm_base_url = "https://api.deepseek.com"

        if llm_api_key and llm_base_url:
            _cognee.config.set_llm_provider("openai")
            _cognee.config.set_llm_model(settings.llm_model)
            _cognee.config.set_llm_api_key(llm_api_key)
            _cognee.config.set_llm_endpoint(llm_base_url)

        # Embedding 配置
        embedding_api_key = settings.embedding_api_key or llm_api_key
        embedding_base_url = settings.embedding_base_url
        if not embedding_base_url and settings.embedding_provider == "dashscope":
            embedding_base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"

        if embedding_api_key and embedding_base_url:
            _cognee.config.set_embedding_provider("openai")
            embedding_model = settings.embedding_model or "text-embedding-3-small"
            # Cognee 内部调用 tiktoken.encoding_for_model() 做 chunking，
            # DashScope 的 text-embedding-v4 等模型不在 tiktoken 映射表中，会抛 KeyError。
            # 手动注册到 cl100k_base，保持 API 调用模型名不变。
            try:
                import tiktoken

                tiktoken.encoding_for_model(embedding_model)
            except KeyError:
                tiktoken.model.MODEL_TO_ENCODING[embedding_model] = "cl100k_base"
            _cognee.config.set_embedding_model(embedding_model)
            _cognee.config.set_embedding_api_key(embedding_api_key)
            _cognee.config.set_embedding_endpoint(embedding_base_url)

        _cognee_status = "ready"
        logger.info(
            "Cognee initialized: data_root=%s, llm_model=%s, embedding_model=%s, has_llm_key=%s, has_embed_key=%s",
            USER_DATA_DIR,
            settings.llm_model,
            settings.embedding_model,
            bool(llm_api_key),
            bool(embedding_api_key),
        )
        return _cognee_status

    except ImportError:
        logger.warning("Cognee not installed")
        _cognee_status = "not_installed"
        return _cognee_status
    except Exception as exc:
        logger.error("Cognee init failed: %s", exc)
        _cognee_status = "error"
        return _cognee_status


async def cognify_loop() -> None:
    """后台异步循环：有新数据时每 X 秒执行一次 cognify。在 FastAPI lifespan 中以 task 启动。"""
    import asyncio

    from app.backend.memory.cognee_admin.datasets import ALL_DATASETS

    while True:
        await asyncio.sleep(COGNEE_COGNIFY_INTERVAL_SEC)

        global _needs_cognify
        if not _needs_cognify or _cognee_status != "ready":
            continue

        _needs_cognify = False
        try:
            import cognee as _cognee

            await _cognee.cognify(datasets=ALL_DATASETS)
            logger.info("Cognee cognify completed: datasets=%s", ALL_DATASETS)
        except Exception as exc:
            logger.error("Cognee cognify failed: %s", exc)
            _needs_cognify = True
