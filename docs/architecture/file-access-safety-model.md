# 文件访问安全模型重构方案（忠实移植 hermes 写黑名单 + 凭据读黑名单）

> 日期：2026-05-29
> 状态：草案
> 基准：hermes-agent `agent/file_safety.py`（已全量通读，下文逐项对照）
> 决策：写模型 100% 忠实移植 hermes；读在 hermes 基础上**有一处明示偏离**（加凭据读黑名单）

## 背景

Lumen 当前所有文件工具（`file_read`/`file_write`/`file_edit`/`file_grep`/`file_ls`/`image_read`）共用 `lib/tools/files.py:66` 的 `_resolve()`，它是 **workspace 读写白名单**：相对/绝对路径都必须落在 `workspace_root`（`file_read` 额外放行 `session-files`）之内，否则报"超出工作区范围"。

这是服务器多租户级强度，套在**本地单用户**伴侣上过度设防：用户连自己桌面的图都读不了，agent 也无法读写工作区外的任何本地文件。改为 hermes 的"默认放行、只拦危险"模型。

## hermes 实测模型（ground truth，逐项）

> 来源：`agent/file_safety.py`（全量通读确认）

### 写：`is_write_denied(path)`（L75-90）
解析 `realpath` 后，命中以下任一即拒：

- **精确文件禁写** `build_write_denied_paths`（L19-43）：
  `~/.ssh/{authorized_keys,id_rsa,id_ed25519,config}`、`{hermes_home}/.env`、
  `~/.bashrc`、`~/.zshrc`、`~/.profile`、`~/.bash_profile`、`~/.zprofile`、
  `~/.netrc`、`~/.pgpass`、`~/.npmrc`、`~/.pypirc`、
  `/etc/sudoers`、`/etc/passwd`、`/etc/shadow`
- **目录前缀禁写** `build_write_denied_prefixes`（L46-61）：
  `~/.ssh`、`~/.aws`、`~/.gnupg`、`~/.kube`、`~/.docker`、`~/.azure`、`~/.config/gh`、
  `/etc/sudoers.d`、`/etc/systemd`
- **可选安全根** `get_safe_write_root`（L64-72）：环境变量 `HERMES_WRITE_SAFE_ROOT`。
  **默认不设** → 写不受根限制；一旦设置 → 写必须在该根内（在黑名单之上再叠加一层白名单）。

### 读：`get_read_block_error(path)`（L93-111）
- **只挡读 hermes 自己的内部缓存**：`~/.hermes/skills/.hub`、`.../index-cache`（防 skill 注入）。
- **凭据读不挡**：`~/.ssh/id_rsa` 等 agent 可读。hermes 对密钥只防写、不防读。

## 目标

- 写：忠实移植 hermes 的"精确文件 + 目录前缀 + 可选安全根"黑名单。
- 读：移植 hermes 的"挡内部缓存"，**并加一处偏离**——补凭据读黑名单，防注入读密钥外泄（Lumen 有联网/抓网页，外泄路径真实存在）。
- 文件工具**不再限制在工作目录**；相对路径仍以 `workspace_root` 解析。
- 保留 `realpath` + 拒符号链接逃逸。

非目标：不改 VL/Vision 调用链、不改 `session-files` 复制逻辑。

## Lumen 移植方案

### 0. 名称映射

| hermes | Lumen |
|--------|-------|
| `hermes_home` (`~/.hermes`) | `USER_DATA_DIR` (`~/.lumen`) |
| `HERMES_WRITE_SAFE_ROOT` | `LUMEN_WRITE_SAFE_ROOT` |
| `~/.hermes/skills/.hub` 读拦截 | Lumen 无对应内部注入缓存 → 该项以 `~/.lumen/config.json`/`.env`（含 key）的读拦截替代 |

### 1. 一份凭据集（读写共用），写再叠加系统/自启动

```python
# lib/tools/_path_safety.py（新建，供 files.py / vision.py / session_files.py 共用）
from pathlib import Path
import os

_HOME = Path.home()
_LUMEN = _HOME / ".lumen"

# 凭据/密钥类：读和写都拦（读是 Lumen 对 hermes 的偏离，写是忠实移植）
_CREDENTIAL_DIRS = [
    _HOME/".ssh", _HOME/".aws", _HOME/".gnupg", _HOME/".kube",
    _HOME/".docker", _HOME/".azure", _HOME/".config"/"gh", _HOME/".config"/"gcloud",
    # 浏览器 profile（cookie / 登录态）
    _HOME/"AppData"/"Local"/"Google"/"Chrome"/"User Data",
    _HOME/"AppData"/"Roaming"/"Mozilla"/"Firefox"/"Profiles",
    _HOME/"AppData"/"Local"/"Microsoft"/"Edge"/"User Data",
]
_CREDENTIAL_FILES = [
    _HOME/".netrc", _HOME/".pgpass", _HOME/".npmrc", _HOME/".pypirc",
    _HOME/".git-credentials",
    _LUMEN/"config.json", _LUMEN/".env",     # Lumen 自身含 API key
]

# 写在凭据集之上额外禁：系统目录 / 自启动 / shell 启动文件（写不可逆，更狠）
# 忠实移植 hermes 的 /etc/* 与 shell-rc，并为 Windows 补充（见“偏离与扩展”）
_WRITE_ONLY_DENY_DIRS = [
    Path("C:/Windows"), Path("C:/Program Files"), Path("C:/Program Files (x86)"),
    Path("/etc/sudoers.d"), Path("/etc/systemd"), Path("/etc"), Path("/bin"), Path("/sbin"),
    _HOME/"AppData"/"Roaming"/"Microsoft"/"Windows"/"Start Menu"/"Programs"/"Startup",
    _LUMEN,                                   # 整个 .lumen 禁写（配置由 save_user_config 受控写）
]
_WRITE_ONLY_DENY_FILES = [
    _HOME/".bashrc", _HOME/".zshrc", _HOME/".profile",
    _HOME/".bash_profile", _HOME/".zprofile",
    Path("/etc/sudoers"), Path("/etc/passwd"), Path("/etc/shadow"),
]

def _hits(resolved: Path, dirs: list[Path], files: list[Path]) -> bool:
    return any(resolved == f.resolve() for f in files if f.exists() or True) \
        or any(resolved == d or _is_within(resolved, d) for d in dirs)
```

### 2. 读 / 写两套入口（替换 workspace 白名单）

忠实对应 hermes 的 `is_write_denied` / `get_read_block_error`：

```python
def is_read_denied(path: str) -> str | None:
    """读黑名单：凭据集（Lumen 对 hermes 的偏离）。命中返回错误信息。"""
    resolved = _real(path)
    if _hits(resolved, _CREDENTIAL_DIRS, _CREDENTIAL_FILES):
        return f"路径受保护，禁止读取：{resolved}"
    return None

def is_write_denied(path: str) -> str | None:
    """写黑名单 = 凭据集 + 系统/自启动/shell-rc + 可选 LUMEN_WRITE_SAFE_ROOT。"""
    resolved = _real(path)
    if _hits(resolved, _CREDENTIAL_DIRS, _CREDENTIAL_FILES):
        return f"路径受保护，禁止写入：{resolved}"
    if _hits(resolved, _WRITE_ONLY_DENY_DIRS, _WRITE_ONLY_DENY_FILES):
        return f"路径受保护，禁止写入：{resolved}"
    safe_root = _safe_write_root()                      # LUMEN_WRITE_SAFE_ROOT，默认 None
    if safe_root and not _is_within(resolved, safe_root):
        return f"写入超出安全根：{resolved}"
    return None
```

`lib/tools/files.py` 的 `_resolve` 拆成：

```python
def _resolve_read(raw_path, workspace_root) -> (resolved, err):
    resolved = _to_real(raw_path, workspace_root)   # 相对→workspace；realpath
    if _is_symlink_escape(raw_path): return resolved, "拒绝符号链接"
    return resolved, is_read_denied(resolved)

def _resolve_write(raw_path, workspace_root) -> (resolved, err):
    resolved = _to_real(raw_path, workspace_root)
    if _is_symlink_escape(raw_path): return resolved, "拒绝符号链接"
    return resolved, is_write_denied(resolved)
```

调用点切换（`lib/tools/files.py`）：
- `file_read`(L175)、`file_grep`(L335)、`file_ls`、`image_read`(`vision.py:183`) → `_resolve_read`
- `file_write`(L267)、`file_edit`(L782) → `_resolve_write`
- 移除 `allow_session_files` 参数：`~/.lumen/session-files` 不在读黑名单里，自然放行。

### 3. 偏离与扩展（明示，区分"忠实移植"和"Lumen 加的"）

| 项 | 来源 |
|----|------|
| 写：`.ssh`/`.aws`/`.gnupg`/`.kube`/`.docker`/`.azure`/`.config/gh` 前缀禁写 | ✅ 忠实移植 hermes |
| 写：shell-rc（`.bashrc` 等）、`/etc/{sudoers,passwd,shadow}`、`/etc/sudoers.d`、`/etc/systemd` 禁写 | ✅ 忠实移植 hermes |
| 写：可选 `LUMEN_WRITE_SAFE_ROOT` 安全根 | ✅ 忠实移植 hermes（默认关） |
| 写：Windows 系统目录 / Startup 自启动 / `.lumen` 禁写 | ⚠️ Lumen 扩展（hermes 是 unix-only，未覆盖 Windows） |
| 读：凭据集禁读（`.ssh`/云凭据/浏览器 profile/`.lumen/config.json` 等） | ⚠️ **明示偏离**（hermes 只挡内部缓存、不挡凭据读；Lumen 加此项防注入读密钥外泄） |

### 4. 保留的防护
- `realpath` 后再判黑名单（防 `..` 逃逸、符号链接指向敏感目录）。
- 拒绝符号链接源。
- `file_write`/`file_edit` 各自原有的大小/类型业务校验不变，本方案只换"路径准入"层。

## 实现计划

| 序号 | 任务 | 文件 |
|------|------|------|
| 1 | 新建 `_path_safety.py`：凭据集 + 写专属集 + `is_read_denied`/`is_write_denied`/`_safe_write_root` | `lib/tools/_path_safety.py` |
| 2 | `_resolve` 拆成 `_resolve_read`/`_resolve_write`，删 workspace 白名单 | `lib/tools/files.py` |
| 3 | 切换 5 个调用点（读 3 / 写 2） | `lib/tools/files.py` |
| 4 | `image_read` 改用 `_resolve_read`，去 `allow_session_files` | `lib/tools/vision.py` |
| 5 | `session_files._is_sensitive_path` 收敛到同一份凭据集 | `lib/chat/session_files.py` |
| 6 | 测试 | `tests/test_path_safety.py`（新建） |

## 测试场景
1. 读桌面/下载/任意普通本地文件 → 成功。
2. 读 `~/.ssh/id_rsa`、`~/.lumen/config.json`、浏览器 cookie 路径 → 被拒（偏离项生效）。
3. 写桌面/任意普通非系统路径 → 成功。
4. 写 `~/.bashrc`、`~/.ssh/*`、`C:\Windows\*`、Startup、`~/.lumen/*` → 被拒。
5. `../../../etc/passwd`、指向 `~/.ssh` 的符号链接 → realpath 命中黑名单被拒。
6. `~/.lumen/session-files/<conv>/x.png` 读 → 放行（image_read 依赖）。
7. 设 `LUMEN_WRITE_SAFE_ROOT=~/work` 后，写其外路径 → 被拒；写其内 → 成功。
8. 相对路径仍以 workspace 解析。

## 风险与取舍
- **写放开后注入写入爆炸半径变大**：注入成功可覆盖用户**非敏感**文件（密钥/系统/自启动已挡）。这是有意的姿态调整，换本地单用户的便利。需用户接受。
- **读加了凭据黑名单**，比 hermes 更稳（hermes 不防密钥读）；代价是若日后真有"读 `.ssh` 下非密钥文件"的正当需求会被一并挡，可按需放宽。
- 若转多用户/服务端：设 `LUMEN_WRITE_SAFE_ROOT` 重新收敛写入，或回退 workspace 白名单。

## 参考
- hermes-agent `agent/file_safety.py` L19-111：`build_write_denied_paths` / `build_write_denied_prefixes` / `get_safe_write_root` / `is_write_denied` / `get_read_block_error`
- `lib/tools/files.py:66` `_resolve`（被替换）
- `lib/chat/session_files.py` `_SENSITIVE_PATHS` / `_is_sensitive_path`（被收敛）
- `lib/tools/vision.py:183` `image_read` 路径校验（同步切换）
