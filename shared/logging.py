"""Lumen 日志配置 — structlog + stdlib 混合方案

设计原则：
1. 开发环境：彩色控制台输出，易读
2. 生产环境：JSON 格式，便于日志收集
3. SQLAlchemy 噪音过滤：只显示 WARNING 以上
4. 结构化上下文：自动注入 request_id, user_id, conversation_id
5. 日志轮转：生产环境写入文件，自动轮转
"""

from __future__ import annotations

import logging
import re
import sys
import uuid
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from typing import Any

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from structlog.contextvars import bind_contextvars, clear_contextvars, merge_contextvars, unbind_contextvars

from shared.path_utils import find_project_root

# ── 敏感信息脱敏 ──────────────────────────────────────────
# 匹配 Telegram Bot API URL 中的 token（bot123456:ABCxxx 格式）
_TELEGRAM_TOKEN_RE = re.compile(r"bot\d+:[A-Za-z0-9_-]{30,}")


def _redact_secrets(logger, method, event_dict):
    """structlog processor：脱敏日志中的敏感 token。"""
    for key in ("event",):
        val = event_dict.get(key)
        if isinstance(val, str) and _TELEGRAM_TOKEN_RE.search(val):
            event_dict[key] = _TELEGRAM_TOKEN_RE.sub("bot***:***", val)
    return event_dict


# ── 日志文件路径 ────────────────────────────────────────────
# 放在项目目录下 logs/ 中，便于开发时 tail -f 查看
LOG_DIR = find_project_root() / "logs"
LOG_FILE = LOG_DIR / "lumen.log"

# ── 上下文变量 ──────────────────────────────────────────────

request_id_var: ContextVar[str] = ContextVar("request_id", default="")

REQUEST_ID_HEADER = "X-Request-ID"

# 调试标志：当设置后会打印更详细的 traceback
_debug_mode: bool = False


# ── structlog 配置 ──────────────────────────────────────────


def setup_logging(json_logs: bool = False, log_level: str = "INFO") -> None:
    """配置 structlog + stdlib 混合日志。

    Args:
        json_logs: True=JSON 格式（生产），False=彩色控制台（开发）
        log_level: 日志级别（DEBUG/INFO/WARNING/ERROR）
    """
    shared_processors: list[structlog.types.Processor] = [
        merge_contextvars,  # 合并上下文变量到每条日志
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        _redact_secrets,  # 脱敏敏感 token
        structlog.processors.CallsiteParameterAdder(
            {
                structlog.processors.CallsiteParameter.FILENAME,
                structlog.processors.CallsiteParameter.LINENO,
                structlog.processors.CallsiteParameter.FUNC_NAME,
            }
        ),
    ]

    # 选择渲染器
    if json_logs:
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    # 配置 structlog
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # 配置控制台 handler（彩色输出）
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=renderer,
            foreign_pre_chain=shared_processors,
        )
    )

    handlers: list[logging.Handler] = [console_handler]

    # ── 文件 handler：纯文本、人类可读、始终启用 ──
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding="utf-8",
    )
    # 文件用纯文本格式（去掉颜色 + ANSI 码），traceback 也用纯文本
    file_renderer = structlog.dev.ConsoleRenderer(
        colors=False,
        exception_formatter=structlog.dev.plain_traceback,
    )
    file_handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=file_renderer,
            foreign_pre_chain=shared_processors,
        )
    )
    handlers.append(file_handler)

    # ── 噪音过滤器：handler 层面硬拦 sqlalchemy 低于 WARNING 的消息 ──
    def _filter_sql(record: logging.LogRecord) -> bool:
        return not (record.name.startswith("sqlalchemy") and record.levelno < logging.WARNING)

    for h in handlers:
        h.addFilter(_filter_sql)

    # 其他噪音源直接设 level（这些不需要 filter，level 设置够用）
    for name in (
        "uvicorn.access",
        "watchfiles",
        "httpx",
        "httpcore",
        "aiosqlite",
        "telegram",
        "httpx_helpers",
    ):
        logging.getLogger(name).setLevel(logging.WARNING)

    for name in logging.Logger.manager.loggerDict:
        if name.startswith("litellm"):
            logging.getLogger(name).setLevel(logging.WARNING)

    # ── 彻底关闭 SQLAlchemy 噪音 ──
    # 不仅 filter，直接把 sqlalchemy 所有 logger 设为 WARNING
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.pool").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.dialects").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.orm").setLevel(logging.WARNING)

    # 配置根日志器
    root_logger = logging.getLogger()
    root_logger.handlers = handlers
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    global _debug_mode
    _debug_mode = log_level.upper() == "DEBUG"


# ── 请求日志中间件 ──────────────────────────────────────────


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """FastAPI 请求日志中间件 — 自动绑定 request_id, user_id, conversation_id。"""

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        # 生成或提取 request_id
        request_id = request.headers.get(REQUEST_ID_HEADER, str(uuid.uuid4())[:8])
        request_id_var.set(request_id)

        # 清空并绑定上下文
        clear_contextvars()
        bind_contextvars(
            request_id=request_id,
            path=request.url.path,
            method=request.method,
            client_host=request.client.host if request.client else None,
        )

        log = structlog.get_logger()
        log.info("request_started")

        try:
            response = await call_next(request)
            log.info("request_completed", status_code=response.status_code)
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        except Exception as exc:
            log.error("request_failed", error=str(exc))
            raise


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """获取带上下文的 logger。"""
    return structlog.get_logger(name)


# ── Chat 上下文绑定 ─────────────────────────────────────────


def bind_chat_context(*, conversation_id: str | None = None, user_id: str | None = None) -> None:
    """在 Agent Loop 中绑定 conversation_id / user_id 到日志上下文

    所有子模块的日志会自动带上这些字段，不需要每个 logger 调用都传一遍。
    """
    kwargs: dict[str, Any] = {}
    if conversation_id:
        kwargs["conversation_id"] = conversation_id
    if user_id:
        kwargs["user_id"] = user_id
    if kwargs:
        bind_contextvars(**kwargs)


def unbind_chat_context() -> None:
    """解除 chat 上下文绑定（请求结束时调用）"""
    unbind_contextvars("conversation_id", "user_id")


def is_debug_mode() -> bool:
    """当前是否处于 DEBUG 日志级别"""
    return _debug_mode
