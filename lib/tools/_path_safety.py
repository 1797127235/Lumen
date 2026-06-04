"""文件路径安全模型 — 黑名单准入（凭据读黑名单 + 写黑名单 + 可选安全根）。

替代原 workspace 白名单模型。默认放行，只拦截危险路径。

- 读黑名单 = 凭据集（密钥/证书/浏览器 profile/lumen 自身配置）
- 写黑名单 = 凭据集 + 系统目录 + 自启动 + shell-rc + 整个 ~/.lumen
- 可选安全根 LUMEN_WRITE_SAFE_ROOT：默认不设；设了则写必须在该根内

路径匹配统一使用 realpath 解析后比较，命中规则：
  resolved == 目标 或 resolved 在目标目录内（is_relative_to）。

不依赖路径是否存在来判定——黑名单目录可能不存在（如没装 aws）。
"""

from __future__ import annotations

import os
from pathlib import Path

_HOME = Path.home()
_LUMEN = _HOME / ".lumen"

# ── 凭据/密钥类：读和写都拦 ──
_CREDENTIAL_DIRS: list[Path] = [
    _HOME / ".ssh",
    _HOME / ".aws",
    _HOME / ".gnupg",
    _HOME / ".kube",
    _HOME / ".docker",
    _HOME / ".azure",
    _HOME / ".config" / "gh",
    _HOME / ".config" / "gcloud",
    # 浏览器 profile（cookie / 登录态）
    _HOME / "AppData" / "Local" / "Google" / "Chrome" / "User Data",
    _HOME / "AppData" / "Roaming" / "Mozilla" / "Firefox" / "Profiles",
    _HOME / "AppData" / "Local" / "Microsoft" / "Edge" / "User Data",
]

_CREDENTIAL_FILES: list[Path] = [
    _HOME / ".netrc",
    _HOME / ".pgpass",
    _HOME / ".npmrc",
    _HOME / ".pypirc",
    _HOME / ".git-credentials",
    _LUMEN / "config.json",
    _LUMEN / ".env",
]

# ── 写在凭据集之上额外禁 ──
# 系统 / 自启动 / shell 启动文件（忠实移植 hermes + Windows 扩展）
_WRITE_ONLY_DENY_DIRS: list[Path] = [
    Path("C:/Windows"),
    Path("C:/Program Files"),
    Path("C:/Program Files (x86)"),
    Path("/etc/sudoers.d"),
    Path("/etc/systemd"),
    Path("/etc"),
    Path("/bin"),
    Path("/sbin"),
    _HOME / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup",
    _LUMEN,  # 整个 .lumen 禁写
]

_WRITE_ONLY_DENY_FILES: list[Path] = [
    _HOME / ".bashrc",
    _HOME / ".zshrc",
    _HOME / ".profile",
    _HOME / ".bash_profile",
    _HOME / ".zprofile",
    Path("/etc/sudoers"),
    Path("/etc/passwd"),
    Path("/etc/shadow"),
]


# ── 内部工具函数 ──


def _is_within(child: Path, parent: Path) -> bool:
    """child 是否在 parent 目录内（child == parent 也算）。"""
    try:
        return child == parent or child.is_relative_to(parent)
    except (TypeError, ValueError):
        # is_relative_to 在某些 Python 版本对混合类型可能出错
        return str(child) == str(parent) or str(child).startswith(str(parent) + os.sep)


def _resolve_path(p: Path) -> Path:
    """对路径做 realpath 解析，不存在时不抛异常。"""
    try:
        return Path(os.path.realpath(str(p)))
    except OSError:
        return p.resolve()


def _hits(resolved: Path, dirs: list[Path], files: list[Path]) -> bool:
    """resolved 是否命中 dirs（目录前缀）或 files（精确文件）。

    黑名单路径也经过 realpath 解析，确保符号链接/junction 被展开后匹配。
    """
    for f in files:
        rf = _resolve_path(f)
        if resolved == rf:
            return True
    for d in dirs:
        rd = _resolve_path(d)
        if _is_within(resolved, rd):
            return True
    return False


def _safe_write_root() -> Path | None:
    """读取 LUMEN_WRITE_SAFE_ROOT 环境变量。默认 None = 不设根限制。"""
    val = os.environ.get("LUMEN_WRITE_SAFE_ROOT", "").strip()
    if not val:
        return None
    return Path(val).resolve()


# ── 公开 API ──


def is_read_denied(path: str) -> str | None:
    """读黑名单：凭据集。命中返回错误信息，None = 放行。"""
    resolved = Path(os.path.realpath(path))
    if _hits(resolved, _CREDENTIAL_DIRS, _CREDENTIAL_FILES):
        return f"路径受保护，禁止读取：{resolved}"
    return None


def is_write_denied(path: str) -> str | None:
    """写黑名单 = 凭据集 + 系统/自启动/shell-rc + 可选 LUMEN_WRITE_SAFE_ROOT。

    命中返回错误信息，None = 放行。
    """
    resolved = Path(os.path.realpath(path))
    # 1. 凭据集（读写共用）
    if _hits(resolved, _CREDENTIAL_DIRS, _CREDENTIAL_FILES):
        return f"路径受保护，禁止写入：{resolved}"
    # 2. 写专属集（系统目录/自启动/shell-rc）
    if _hits(resolved, _WRITE_ONLY_DENY_DIRS, _WRITE_ONLY_DENY_FILES):
        return f"路径受保护，禁止写入：{resolved}"
    # 3. 可选安全根
    safe_root = _safe_write_root()
    if safe_root and not _is_within(resolved, safe_root):
        return f"写入超出安全根：{resolved}"
    return None
