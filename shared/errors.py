"""Lumen 统一错误体系

设计原则：
1. 所有业务异常统一用 LumenError，不再裸抛 ValueError / RuntimeError
2. 错误包含 severity（严重级别）、category（分类）、retryable（是否可重试）
3. 自动映射到对应的 HTTP 状态码，Router 层可直接转 HTTPException
4. 提供 wrap() 包装第三方异常，保留原始堆栈
5. 提供 FastAPI exception_handler 注册函数，一行代码全局生效

迁移路径：
- 新业务代码直接 raise LumenError("CODE", message="...")
- 旧代码的 bare except Exception 可逐步替换为 LumenError.wrap()
- Router 层的 HTTPException 可逐步替换为 LumenError + 统一 handler
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from fastapi import HTTPException, Request  # pyright: ignore[reportMissingImports]
from fastapi.responses import JSONResponse  # pyright: ignore[reportMissingImports]
from starlette.status import (  # pyright: ignore[reportMissingImports]
    HTTP_400_BAD_REQUEST,
    HTTP_401_UNAUTHORIZED,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
    HTTP_429_TOO_MANY_REQUESTS,
    HTTP_500_INTERNAL_SERVER_ERROR,
    HTTP_502_BAD_GATEWAY,
    HTTP_503_SERVICE_UNAVAILABLE,
    HTTP_504_GATEWAY_TIMEOUT,
)

from shared.logging import get_logger

logger = get_logger(__name__)


# ── 枚举定义 ────────────────────────────────────────────────


class ErrorSeverity(StrEnum):
    """错误严重级别"""

    CRITICAL = "critical"  # 系统可能不可用，需要立即处理
    DEGRADED = "degraded"  # 功能降级，核心流程仍可继续
    COSMETIC = "cosmetic"  # 非关键问题，不影响核心功能


class ErrorCategory(StrEnum):
    """错误分类"""

    LLM = "llm"
    NETWORK = "network"
    FILESYSTEM = "filesystem"
    DATABASE = "database"
    CONFIG = "config"
    AUTH = "auth"
    VALIDATION = "validation"
    SEARCH = "search"
    MCP = "mcp"
    UNKNOWN = "unknown"


# ── 错误定义 ────────────────────────────────────────────────


@dataclass(frozen=True)
class ErrorDef:
    """错误元数据定义"""

    severity: ErrorSeverity
    category: ErrorCategory
    default_message: str = ""
    retryable: bool = False
    http_status: int = HTTP_500_INTERNAL_SERVER_ERROR


# 错误码注册表（参考 openhanako ERROR_DEFS）
_ERROR_REGISTRY: dict[str, ErrorDef] = {
    # ── LLM ──
    "LLM_TIMEOUT": ErrorDef(
        severity=ErrorSeverity.DEGRADED,
        category=ErrorCategory.LLM,
        default_message="LLM 请求超时",
        retryable=True,
        http_status=HTTP_504_GATEWAY_TIMEOUT,
    ),
    "LLM_RATE_LIMITED": ErrorDef(
        severity=ErrorSeverity.DEGRADED,
        category=ErrorCategory.LLM,
        default_message="LLM 速率限制，请稍后重试",
        retryable=True,
        http_status=HTTP_429_TOO_MANY_REQUESTS,
    ),
    "LLM_EMPTY_RESPONSE": ErrorDef(
        severity=ErrorSeverity.DEGRADED,
        category=ErrorCategory.LLM,
        default_message="LLM 返回空响应",
        retryable=True,
        http_status=HTTP_502_BAD_GATEWAY,
    ),
    "LLM_AUTH_FAILED": ErrorDef(
        severity=ErrorSeverity.DEGRADED,
        category=ErrorCategory.LLM,
        default_message="LLM 认证失败，请检查 API Key",
        retryable=False,
        http_status=HTTP_401_UNAUTHORIZED,
    ),
    "LLM_SLOW_RESPONSE": ErrorDef(
        severity=ErrorSeverity.COSMETIC,
        category=ErrorCategory.LLM,
        default_message="LLM 响应较慢",
        retryable=False,
        http_status=HTTP_500_INTERNAL_SERVER_ERROR,
    ),
    # ── Network ──
    "FETCH_TIMEOUT": ErrorDef(
        severity=ErrorSeverity.DEGRADED,
        category=ErrorCategory.NETWORK,
        default_message="网络请求超时",
        retryable=True,
        http_status=HTTP_504_GATEWAY_TIMEOUT,
    ),
    "FETCH_SERVER_ERROR": ErrorDef(
        severity=ErrorSeverity.DEGRADED,
        category=ErrorCategory.NETWORK,
        default_message="上游服务错误",
        retryable=True,
        http_status=HTTP_502_BAD_GATEWAY,
    ),
    # ── Filesystem ──
    "FS_PERMISSION": ErrorDef(
        severity=ErrorSeverity.CRITICAL,
        category=ErrorCategory.FILESYSTEM,
        default_message="文件权限不足",
        retryable=False,
        http_status=HTTP_500_INTERNAL_SERVER_ERROR,
    ),
    "FS_NOT_FOUND": ErrorDef(
        severity=ErrorSeverity.DEGRADED,
        category=ErrorCategory.FILESYSTEM,
        default_message="文件不存在",
        retryable=False,
        http_status=HTTP_404_NOT_FOUND,
    ),
    "FS_COPY_FAILED": ErrorDef(
        severity=ErrorSeverity.CRITICAL,
        category=ErrorCategory.FILESYSTEM,
        default_message="文件复制失败",
        retryable=True,
        http_status=HTTP_500_INTERNAL_SERVER_ERROR,
    ),
    # ── Config ──
    "CONFIG_MISSING_KEY": ErrorDef(
        severity=ErrorSeverity.CRITICAL,
        category=ErrorCategory.CONFIG,
        default_message="缺少必要的配置项",
        retryable=False,
        http_status=HTTP_400_BAD_REQUEST,
    ),
    "CONFIG_PARSE": ErrorDef(
        severity=ErrorSeverity.CRITICAL,
        category=ErrorCategory.CONFIG,
        default_message="配置文件解析失败",
        retryable=False,
        http_status=HTTP_500_INTERNAL_SERVER_ERROR,
    ),
    # ── Database ──
    "DB_ERROR": ErrorDef(
        severity=ErrorSeverity.CRITICAL,
        category=ErrorCategory.DATABASE,
        default_message="数据库操作失败",
        retryable=False,
        http_status=HTTP_500_INTERNAL_SERVER_ERROR,
    ),
    "DB_NOT_FOUND": ErrorDef(
        severity=ErrorSeverity.DEGRADED,
        category=ErrorCategory.DATABASE,
        default_message="记录不存在",
        retryable=False,
        http_status=HTTP_404_NOT_FOUND,
    ),
    # ── Search ──
    "SEARCH_INDEX_ERROR": ErrorDef(
        severity=ErrorSeverity.DEGRADED,
        category=ErrorCategory.SEARCH,
        default_message="搜索索引异常",
        retryable=True,
        http_status=HTTP_503_SERVICE_UNAVAILABLE,
    ),
    # ── MCP ──
    "MCP_CONNECTION_FAILED": ErrorDef(
        severity=ErrorSeverity.DEGRADED,
        category=ErrorCategory.MCP,
        default_message="MCP 服务器连接失败",
        retryable=True,
        http_status=HTTP_502_BAD_GATEWAY,
    ),
    "MCP_TOOL_CALL_FAILED": ErrorDef(
        severity=ErrorSeverity.DEGRADED,
        category=ErrorCategory.MCP,
        default_message="MCP 工具调用失败",
        retryable=True,
        http_status=HTTP_502_BAD_GATEWAY,
    ),
    # ── Validation / Auth ──
    "VALIDATION_ERROR": ErrorDef(
        severity=ErrorSeverity.DEGRADED,
        category=ErrorCategory.VALIDATION,
        default_message="请求参数校验失败",
        retryable=False,
        http_status=HTTP_400_BAD_REQUEST,
    ),
    "NOT_FOUND": ErrorDef(
        severity=ErrorSeverity.DEGRADED,
        category=ErrorCategory.VALIDATION,
        default_message="资源不存在",
        retryable=False,
        http_status=HTTP_404_NOT_FOUND,
    ),
    "FORBIDDEN": ErrorDef(
        severity=ErrorSeverity.DEGRADED,
        category=ErrorCategory.AUTH,
        default_message="无权访问该资源",
        retryable=False,
        http_status=HTTP_403_FORBIDDEN,
    ),
    "CONFLICT": ErrorDef(
        severity=ErrorSeverity.DEGRADED,
        category=ErrorCategory.VALIDATION,
        default_message="资源冲突",
        retryable=False,
        http_status=HTTP_409_CONFLICT,
    ),
    # ── Capacity ──
    "LOCK_CAPACITY_EXCEEDED": ErrorDef(
        severity=ErrorSeverity.CRITICAL,
        category=ErrorCategory.UNKNOWN,
        default_message="系统负载过高，请稍后重试",
        retryable=True,
        http_status=HTTP_503_SERVICE_UNAVAILABLE,
    ),
    # ── Memory ──
    "MEMORY_COMPILE_FAILED": ErrorDef(
        severity=ErrorSeverity.DEGRADED,
        category=ErrorCategory.UNKNOWN,
        default_message="记忆编译失败",
        retryable=True,
        http_status=HTTP_500_INTERNAL_SERVER_ERROR,
    ),
    # ── Fallback ──
    "UNKNOWN": ErrorDef(
        severity=ErrorSeverity.DEGRADED,
        category=ErrorCategory.UNKNOWN,
        default_message="未知错误",
        retryable=False,
        http_status=HTTP_500_INTERNAL_SERVER_ERROR,
    ),
}


# ── 主异常类 ────────────────────────────────────────────────


class LumenError(Exception):
    """Lumen 统一业务异常

    Usage:
        # 1. 使用预定义错误码
        raise LumenError("LLM_TIMEOUT", message="请求 gemini-pro 超时")

        # 2. 包装第三方异常（保留原始堆栈）
        except httpx.TimeoutException as exc:
            raise LumenError.wrap(exc, "FETCH_TIMEOUT")

        # 3. 在 Router 层统一转 HTTPException
        except LumenError as exc:
            raise exc.to_http_exception()

        # 4. 或直接让 FastAPI 全局 handler 处理（推荐）
        app.add_exception_handler(LumenError, handle_lumen_error)
    """

    def __init__(
        self,
        code: str,
        *,
        message: str | None = None,
        context: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        self.code = code
        self._def = _ERROR_REGISTRY.get(code) or _ERROR_REGISTRY["UNKNOWN"]
        self.message = message or self._def.default_message
        self.severity = self._def.severity
        self.category = self._def.category
        self.retryable = self._def.retryable
        self.http_status = self._def.http_status
        self.context = context or {}
        self.trace_id = self._generate_trace_id()

        super().__init__(self.message)
        if cause is not None:
            self.__cause__ = cause

    # ── 属性 ──

    @property
    def detail(self) -> str:
        """返回给前端/用户的可读消息"""
        return self.message

    # ── 序列化 ──

    def to_dict(self, *, include_trace: bool = False) -> dict[str, Any]:
        """转为字典（供日志或 JSON 响应）"""
        result: dict[str, Any] = {
            "error": True,
            "code": self.code,
            "message": self.message,
            "severity": self.severity.value,
            "category": self.category.value,
            "retryable": self.retryable,
            "trace_id": self.trace_id,
        }
        if self.context:
            result["context"] = self.context
        if include_trace and self.__cause__:
            result["cause"] = f"{type(self.__cause__).__name__}: {self.__cause__}"
        return result

    def to_http_exception(self) -> HTTPException:
        """转为 FastAPI HTTPException

        用于还没接入全局 exception_handler 的旧代码，或需要显式控制的地方。
        """
        return HTTPException(
            status_code=self.http_status,
            detail=self.to_dict(),
        )

    # ── 类方法 ──

    @classmethod
    def wrap(
        cls,
        exc: Exception,
        fallback_code: str = "UNKNOWN",
        *,
        context: dict[str, Any] | None = None,
        message: str | None = None,
    ) -> LumenError:
        """包装任意异常为 LumenError

        如果 exc 已经是 LumenError，直接返回原实例（不嵌套包装）。
        """
        if isinstance(exc, cls):
            return exc

        # 根据异常类型智能映射（可扩展）
        code = _infer_code_from_exception(exc) or fallback_code
        msg = message or str(exc) or fallback_code

        lumen_err = cls(
            code=code,
            message=msg,
            context=context,
            cause=exc,
        )
        # 保留原始 traceback
        if exc.__traceback__ is not None:
            lumen_err.__traceback__ = exc.__traceback__
        return lumen_err

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LumenError:
        """从字典反序列化（用于跨进程/跨服务传递）"""
        return cls(
            code=data.get("code", "UNKNOWN"),
            message=data.get("message"),
            context=data.get("context"),
        )

    # ── 内部 ──

    @staticmethod
    def _generate_trace_id() -> str:
        import random

        return random.randbytes(4).hex()

    def __repr__(self) -> str:
        return (
            f"LumenError(code={self.code!r}, severity={self.severity.value!r}, "
            f"category={self.category.value!r}, retryable={self.retryable}, "
            f"trace_id={self.trace_id!r}, message={self.message!r})"
        )


# ── 便捷构造函数 ────────────────────────────────────────────
# 用于常见场景的快速创建，减少样板代码


def not_found(resource: str = "资源", *, context: dict[str, Any] | None = None) -> LumenError:
    """资源不存在"""
    return LumenError("NOT_FOUND", message=f"{resource}不存在", context=context)


def forbidden(resource: str = "该资源", *, context: dict[str, Any] | None = None) -> LumenError:
    """无权访问"""
    return LumenError("FORBIDDEN", message=f"无权访问{resource}", context=context)


def bad_request(detail: str = "请求参数无效", *, context: dict[str, Any] | None = None) -> LumenError:
    """请求参数错误"""
    return LumenError("VALIDATION_ERROR", message=detail, context=context)


def conflict(detail: str = "资源冲突", *, context: dict[str, Any] | None = None) -> LumenError:
    """资源冲突"""
    return LumenError("CONFLICT", message=detail, context=context)


def llm_timeout(model: str | None = None, *, context: dict[str, Any] | None = None) -> LumenError:
    """LLM 超时"""
    msg = f"{model} 请求超时" if model else "LLM 请求超时"
    return LumenError("LLM_TIMEOUT", message=msg, context=context)


def llm_auth_failed(provider: str | None = None, *, context: dict[str, Any] | None = None) -> LumenError:
    """LLM 认证失败"""
    msg = f"{provider} 认证失败，请检查 API Key" if provider else "LLM 认证失败，请检查 API Key"
    return LumenError("LLM_AUTH_FAILED", message=msg, context=context)


def config_missing_key(key: str, *, context: dict[str, Any] | None = None) -> LumenError:
    """缺少配置项"""
    return LumenError(
        "CONFIG_MISSING_KEY",
        message=f"未配置 {key}。请在设置页面配置，或在 .env 文件中设置对应环境变量。",
        context={"key": key, **(context or {})},
    )


def db_not_found(table: str = "记录", *, context: dict[str, Any] | None = None) -> LumenError:
    """数据库记录不存在"""
    return LumenError("DB_NOT_FOUND", message=f"{table}不存在", context=context)


# ── 智能映射 ────────────────────────────────────────────────


def _infer_code_from_exception(exc: Exception) -> str | None:
    """根据异常类型推断错误码"""
    exc_type = type(exc).__name__
    exc_module = type(exc).__module__
    exc_msg = str(exc).lower()

    # LLM 相关
    if "timeout" in exc_msg or "timed out" in exc_msg:
        return "LLM_TIMEOUT" if "llm" in exc_module or "openai" in exc_module else "FETCH_TIMEOUT"
    if "rate limit" in exc_msg or "ratelimit" in exc_msg or "too many requests" in exc_msg:
        return "LLM_RATE_LIMITED"
    if "authentication" in exc_msg or "auth" in exc_msg or "api key" in exc_msg or "unauthorized" in exc_msg:
        return "LLM_AUTH_FAILED"
    if exc_type in ("APITimeoutError", "TimeoutException", "ReadTimeout"):
        return "FETCH_TIMEOUT"

    # 文件系统
    if exc_type in ("PermissionError",):
        return "FS_PERMISSION"
    if exc_type in ("FileNotFoundError",):
        return "FS_NOT_FOUND"

    # 数据库
    if exc_module.startswith("sqlalchemy") or exc_type in ("OperationalError", "IntegrityError"):
        return "DB_ERROR"

    # 网络
    if exc_type in ("ConnectionError", "ConnectTimeout"):
        return "FETCH_TIMEOUT"

    return None


# ── FastAPI 集成 ────────────────────────────────────────────


def handle_lumen_error(_request: Request, exc: LumenError) -> JSONResponse:
    """FastAPI 全局异常处理器

    注册方式（在 main.py 的 create_app() 中）：
        from shared.errors import handle_lumen_error, LumenError
        app.add_exception_handler(LumenError, handle_lumen_error)

    效果：所有未被路由层捕获的 LumenError 自动转为 JSON 响应，
    不再需要在每个路由里手写 try/except + HTTPException。
    """
    # 严重错误记日志
    if exc.severity == ErrorSeverity.CRITICAL:
        logger.error(
            "Critical error handled",
            code=exc.code,
            trace_id=exc.trace_id,
            category=exc.category.value,
            context=exc.context,
            exc_info=exc.__cause__ is not None,
        )
    else:
        logger.warning(
            "Error handled",
            code=exc.code,
            trace_id=exc.trace_id,
            category=exc.category.value,
            retryable=exc.retryable,
        )

    return JSONResponse(
        status_code=exc.http_status,
        content=exc.to_dict(include_trace=exc.severity == ErrorSeverity.CRITICAL),
    )


def handle_fallback_error(_request: Request, exc: Exception) -> JSONResponse:
    """兜底异常处理器 — 捕获所有未被处理的非 LumenError

    建议注册在 LumenError handler 之后：
        app.add_exception_handler(Exception, handle_fallback_error)

    把裸 Exception 包装为 LumenError(UNKNOWN) 后统一响应格式，
    避免前端收到非结构化的 500 错误。
    """
    lumen_err = LumenError.wrap(exc, "UNKNOWN")
    logger.error(
        "Unhandled exception wrapped",
        code=lumen_err.code,
        trace_id=lumen_err.trace_id,
        original_type=type(exc).__name__,
        exc_info=True,
    )
    return JSONResponse(
        status_code=lumen_err.http_status,
        content=lumen_err.to_dict(include_trace=True),
    )
