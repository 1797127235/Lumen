"""工具策略层 — 路径、循环、审批、预算、结果标准化。

所有策略都接收 (ToolDefinition, ToolRuntimeContext, args) 三元组，
返回 (ok: bool, message: str, metadata: dict)。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.modules.agent.tools.core.context import ToolRuntimeContext
from backend.modules.agent.tools.core.definitions import ToolDefinition

# ── PathPolicy ──


class PathPolicy:
    """路径策略：解析、归一化、workspace 边界校验。

    规则：
    1. 相对路径永远相对 ctx.cwd
    2. 解析后必须在 ctx.workspace_root 内
    3. 若 workspace_root 缺失，直接报配置错误
    4. 不允许默认退回 HOME
    """

    _SENSITIVE_NAMES = frozenset(
        {
            ".env",
            ".env.local",
            ".env.production",
            ".env.development",
            "id_rsa",
            "id_ed25519",
            "id_ecdsa",
            "id_dsa",
            ".ssh",
            ".aws",
            ".azure",
            ".gcp",
            ".kube",
            "credentials.json",
            "service_account.json",
            "token.json",
            "secrets.yaml",
            "secrets.yml",
            "vault.yml",
        }
    )

    @classmethod
    def resolve(cls, raw_path: str, ctx: ToolRuntimeContext) -> tuple[Path | None, str]:
        """解析路径，返回 (resolved_path, error_message)。"""

        if not raw_path:
            return None, "路径不能为空"

        if ctx.workspace_root is None:
            return None, "未配置工作区（workspace_root 缺失），无法解析路径"

        p = Path(raw_path).expanduser()

        if not p.is_absolute():
            base = ctx.cwd or ctx.workspace_root
            p = base / p

        try:
            p = p.resolve()
        except (OSError, ValueError) as exc:
            return None, f"路径解析失败: {exc}"

        # 遍历防护：必须在 workspace_root 内
        try:
            p.relative_to(ctx.workspace_root.resolve())
        except ValueError:
            return None, (f"路径越界: '{raw_path}' 解析为 '{p}'，" f"超出工作区 '{ctx.workspace_root}'")

        # 敏感路径检查
        name = p.name
        if name in cls._SENSITIVE_NAMES:
            return None, f"禁止访问敏感路径: {name}"

        return p, ""

    @classmethod
    def validate(cls, path: Path, allow_write: bool = False) -> tuple[bool, str]:
        """验证文件/目录是否可访问。"""
        if path.is_dir() and not allow_write:
            return False, f"{path.name} 是目录，请使用 file_list 列出内容"
        return True, ""


# ── LoopGuardPolicy ──


class LoopGuardPolicy:
    """循环保护策略：检测重复工具调用和连续探索死循环。

    规则：
    1. 同一工具 + 同 path 连续失败 2 次 → 阻断
    2. 连续 6 次文件类工具调用（无论成败）→ 阻断
    3. 非读工具调用后重置连续计数
    """

    _FILE_TOOL_PREFIXES = ("file_", "read_", "search_", "list_")
    _LOOP_THRESHOLD = 2
    _EXPLORATION_LIMIT = 6

    @classmethod
    def check(cls, tool: ToolDefinition, ctx: ToolRuntimeContext, args: dict[str, Any]) -> tuple[bool, str]:
        """检查本次调用是否触发循环，返回 (allow, message)。"""

        calls = ctx.tool_state.get("_tool_calls", [])
        if not calls:
            return True, ""

        tool_name = tool.name
        current_path = args.get("path", "")

        # 规则 1: 同一工具 + 同 path 连续失败
        consecutive_fails = 0
        for call in reversed(calls):
            if call.get("tool") == tool_name and call.get("path") == current_path and not call.get("ok", True):
                consecutive_fails += 1
            else:
                break

        if consecutive_fails >= cls._LOOP_THRESHOLD:
            return False, (
                f"{tool_name} 对路径 '{current_path}' 已连续失败 {consecutive_fails} 次。"
                "请停止重复尝试，确认路径是否正确或换个方式处理。"
            )

        # 规则 2: 连续文件工具调用过多（无论成败）
        if tool_name.startswith(cls._FILE_TOOL_PREFIXES):
            recent_file_tools = 0
            for call in reversed(calls):
                if call["tool"].startswith(cls._FILE_TOOL_PREFIXES):
                    recent_file_tools += 1
                else:
                    break
            if recent_file_tools >= cls._EXPLORATION_LIMIT:
                return False, (
                    "已连续进行多次文件操作仍未找到目标。" "请停止探索，直接向用户说明情况或请求更明确的路径。"
                )

        return True, ""

    @classmethod
    def record(cls, tool: ToolDefinition, ctx: ToolRuntimeContext, args: dict[str, Any], ok: bool) -> None:
        """记录本次调用。"""

        fingerprint = {
            "tool": tool.name,
            "path": args.get("path", ""),
            "ok": ok,
        }
        calls = ctx.tool_state.setdefault("_tool_calls", [])
        calls.append(fingerprint)
        if len(calls) > 50:
            ctx.tool_state["_tool_calls"] = calls[-25:]


# ── BudgetPolicy ──


class BudgetPolicy:
    """预算策略：限制工具调用次数。"""

    @classmethod
    def check(cls, tool: ToolDefinition, ctx: ToolRuntimeContext) -> tuple[bool, str]:
        """检查预算是否耗尽。"""
        used = ctx.usage_budget.get("tool_calls", 0)
        limit = ctx.usage_budget.get("tool_calls_limit", 6)
        if used >= limit:
            return False, f"工具调用次数已达上限 ({used}/{limit})，请直接回答用户"
        return True, ""

    @classmethod
    def consume(cls, ctx: ToolRuntimeContext) -> None:
        """消耗一次预算。"""
        ctx.usage_budget["tool_calls"] = ctx.usage_budget.get("tool_calls", 0) + 1


# ── ResultPolicy ──


class ResultPolicy:
    """结果策略：标准化工具输出格式。"""

    @staticmethod
    def format_error(message: str, code: str = "") -> str:
        """格式化错误信息。"""
        if code:
            return f"[工具错误/{code}] {message}"
        return f"[工具错误] {message}"

    @staticmethod
    def format_success(data: str, metadata: dict[str, Any] | None = None) -> str:
        """格式化成功结果。"""
        return data


# ── ApprovalPolicy ──


class ApprovalPolicy:
    """审批策略：检查是否需要用户确认。
    当前实现：写操作工具默认需要审批标记，实际审批逻辑在 UI 层。
    """

    @classmethod
    def check(cls, tool: ToolDefinition) -> tuple[bool, str]:
        """返回 (needs_approval, reason)。"""
        if tool.requires_approval:
            return True, f"{tool.name} 需要用户确认"
        return False, ""
