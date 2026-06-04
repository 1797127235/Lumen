"""test_path_safety — 覆盖文件访问安全模型的黑名单行为。

8 个场景：
1. 读桌面/任意普通本地路径 → 放行
2. 读凭据集路径 → 被拒
3. 写桌面/任意普通非系统路径 → 放行
4. 写系统目录/自启动/shell-rc/凭据集 → 被拒
5. ../逃逸 + 符号链接 → realpath 后命中被拒
6. 读 ~/.lumen/session-files → 放行
7. LUMEN_WRITE_SAFE_ROOT → 写其外被拒、写其内放行
8. 黑名单目录不存在时仍能命中拦截
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from lib.tools._path_safety import (
    is_read_denied,
    is_write_denied,
)

# ── 场景 1: 读桌面/任意普通本地路径 → 放行 ──


class TestReadAllowed:
    def test_read_desktop(self):
        desktop = Path.home() / "Desktop"
        assert is_read_denied(str(desktop / "notes.txt")) is None

    def test_read_arbitrary_local_path(self):
        with tempfile.TemporaryDirectory() as td:
            assert is_read_denied(os.path.join(td, "file.txt")) is None

    def test_read_workspace_source_file(self):
        assert is_read_denied(str(Path.cwd() / "main.py")) is None

    def test_read_downloads(self):
        downloads = Path.home() / "Downloads"
        assert is_read_denied(str(downloads / "archive.zip")) is None

    def test_read_tmp(self):
        assert is_read_denied(os.path.join(tempfile.gettempdir(), "tmpfile")) is None


# ── 场景 2: 读凭据集路径 → 被拒 ──


class TestReadDenied:
    @pytest.mark.parametrize(
        "subpath",
        ["id_rsa", "id_ed25519", "config", "authorized_keys", "known_hosts"],
    )
    def test_read_ssh(self, subpath: str):
        p = str(Path.home() / ".ssh" / subpath)
        assert is_read_denied(p) is not None

    def test_read_aws_credentials(self):
        p = str(Path.home() / ".aws" / "credentials")
        assert is_read_denied(p) is not None

    def test_read_lumen_config_json(self):
        p = str(Path.home() / ".lumen" / "config.json")
        assert is_read_denied(p) is not None

    def test_read_lumen_env(self):
        p = str(Path.home() / ".lumen" / ".env")
        assert is_read_denied(p) is not None

    def test_read_gnupg(self):
        p = str(Path.home() / ".gnupg" / "secring.gpg")
        assert is_read_denied(p) is not None

    def test_read_kube_config(self):
        p = str(Path.home() / ".kube" / "config")
        assert is_read_denied(p) is not None

    def test_read_netrc(self):
        p = str(Path.home() / ".netrc")
        assert is_read_denied(p) is not None

    def test_read_git_credentials(self):
        p = str(Path.home() / ".git-credentials")
        assert is_read_denied(p) is not None

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only browser paths")
    def test_read_chrome_cookies(self):
        p = str(Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "User Data" / "Default" / "Cookies")
        assert is_read_denied(p) is not None

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only browser paths")
    def test_read_edge_cookies(self):
        p = str(Path.home() / "AppData" / "Local" / "Microsoft" / "Edge" / "User Data" / "Default" / "Cookies")
        assert is_read_denied(p) is not None

    def test_read_gh_config(self):
        p = str(Path.home() / ".config" / "gh" / "hosts.yml")
        assert is_read_denied(p) is not None


# ── 场景 3: 写桌面/任意普通非系统路径 → 放行 ──


class TestWriteAllowed:
    def test_write_desktop(self):
        p = str(Path.home() / "Desktop" / "output.txt")
        assert is_write_denied(p) is None

    def test_write_arbitrary_local_path(self):
        with tempfile.TemporaryDirectory() as td:
            assert is_write_denied(os.path.join(td, "output.txt")) is None

    def test_write_workspace(self):
        assert is_write_denied(str(Path.cwd() / "output.txt")) is None

    def test_write_downloads(self):
        p = str(Path.home() / "Downloads" / "file.zip")
        assert is_write_denied(p) is None

    def test_write_tmp(self):
        assert is_write_denied(os.path.join(tempfile.gettempdir(), "tmpfile")) is None


# ── 场景 4: 写系统目录/自启动/shell-rc/凭据集/整个 .lumen → 被拒 ──


class TestWriteDenied:
    @pytest.mark.parametrize(
        "shell_file",
        [".bashrc", ".zshrc", ".profile", ".bash_profile", ".zprofile"],
    )
    def test_write_shell_rc(self, shell_file: str):
        p = str(Path.home() / shell_file)
        assert is_write_denied(p) is not None

    def test_write_ssh_anything(self):
        p = str(Path.home() / ".ssh" / "random_file")
        assert is_write_denied(p) is not None

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only paths")
    def test_write_windows_system(self):
        p = str(Path("C:/Windows/System32/drivers/etc/hosts"))
        assert is_write_denied(p) is not None

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only paths")
    def test_write_program_files(self):
        p = str(Path("C:/Program Files/SomeApp/app.exe"))
        assert is_write_denied(p) is not None

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only paths")
    def test_write_windows_startup(self):
        startup = Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        p = str(startup / "evil.bat")
        assert is_write_denied(p) is not None

    def test_write_lumen_anything(self):
        p = str(Path.home() / ".lumen" / "anything.txt")
        assert is_write_denied(p) is not None

    def test_write_lumen_subdir(self):
        p = str(Path.home() / ".lumen" / "sub" / "deep" / "file.txt")
        assert is_write_denied(p) is not None

    def test_write_aws_credentials(self):
        p = str(Path.home() / ".aws" / "credentials")
        assert is_write_denied(p) is not None

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only paths")
    def test_write_etc_sudoers(self):
        assert is_write_denied("/etc/sudoers") is not None

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only paths")
    def test_write_etc_passwd(self):
        assert is_write_denied("/etc/passwd") is not None

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only paths")
    def test_write_etc_shadow(self):
        assert is_write_denied("/etc/shadow") is not None

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only paths")
    def test_write_etc_sudoers_d(self):
        assert is_write_denied("/etc/sudoers.d/evil") is not None

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only paths")
    def test_write_etc_systemd(self):
        assert is_write_denied("/etc/systemd/system/evil.service") is not None

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only paths")
    def test_write_bin(self):
        assert is_write_denied("/bin/evil") is not None

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only paths")
    def test_write_sbin(self):
        assert is_write_denied("/sbin/evil") is not None

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only paths")
    def test_write_etc_generic(self):
        assert is_write_denied("/etc/some_config") is not None


# ── 场景 5: realpath 后命中黑名单（../逃逸 + 符号链接） ──


class TestRealpathBypassPrevention:
    def test_dotdot_escape_to_etc_passwd(self):
        """../../../etc/passwd 经 realpath 后仍命中写黑名单"""
        p = os.path.normpath(str(Path.cwd() / ".." / ".." / ".." / "etc" / "passwd"))
        if sys.platform == "win32":
            pytest.skip("POSIX-only path")
        assert is_write_denied(p) is not None

    def test_symlink_into_ssh_dir(self):
        """创建指向 ~/.ssh 的符号链接，通过它读取仍被拒"""
        ssh_dir = Path.home() / ".ssh"
        link_dir = Path.home() / "Desktop" / "link_to_ssh"
        try:
            # 创建符号链接（需要目录存在或使用 target_is_directory）
            # 在 Windows 上创建符号链接可能需要管理员权限，所以如果失败就跳过
            link_dir.parent.mkdir(parents=True, exist_ok=True)
            try:
                link_dir.symlink_to(ssh_dir, target_is_directory=True)
            except OSError:
                pytest.skip("Cannot create symlink on this system")

            # 通过符号链接路径读取 ~/.ssh/id_rsa
            p = str(link_dir / "id_rsa")
            result = is_read_denied(p)
            # 无论符号链接是否存在，realpath 都会解析
            # 解析后路径应在 ~/.ssh/ 下，命中凭据目录
            assert result is not None, f"Expected denial for symlink path {p}"
        finally:
            if link_dir.exists() or link_dir.is_symlink():
                link_dir.unlink()


# ── 场景 6: 读 ~/.lumen/session-files → 放行 ──


class TestSessionFilesAllowed:
    def test_read_session_files_image(self):
        p = str(Path.home() / ".lumen" / "session-files" / "conv-123" / "x.png")
        assert is_read_denied(p) is None

    def test_read_session_files_text(self):
        p = str(Path.home() / ".lumen" / "session-files" / "conv-456" / "data.txt")
        assert is_read_denied(p) is None


# ── 场景 7: LUMEN_WRITE_SAFE_ROOT ──


class TestSafeWriteRoot:
    def test_write_inside_safe_root_allowed(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"LUMEN_WRITE_SAFE_ROOT": td}):
            # 重新加载模块以读取新环境变量
            import importlib

            import lib.tools._path_safety as ps

            importlib.reload(ps)
            try:
                p = os.path.join(td, "output.txt")
                assert ps.is_write_denied(p) is None
            finally:
                # 清理环境变量，重新加载
                os.environ.pop("LUMEN_WRITE_SAFE_ROOT", None)
                importlib.reload(ps)

    def test_write_outside_safe_root_denied(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"LUMEN_WRITE_SAFE_ROOT": td}):
            import importlib

            import lib.tools._path_safety as ps

            importlib.reload(ps)
            try:
                # 写到安全根之外的桌面路径
                p = str(Path.home() / "Desktop" / "outside.txt")
                assert ps.is_write_denied(p) is not None
            finally:
                os.environ.pop("LUMEN_WRITE_SAFE_ROOT", None)
                importlib.reload(ps)

    def test_write_without_safe_root_uses_blacklist_only(self):
        """默认不设 LUMEN_WRITE_SAFE_ROOT → 写桌面不被拒（只检查黑名单）"""
        os.environ.pop("LUMEN_WRITE_SAFE_ROOT", None)
        import importlib

        import lib.tools._path_safety as ps

        importlib.reload(ps)
        p = str(Path.home() / "Desktop" / "normal.txt")
        assert ps.is_write_denied(p) is None


# ── 场景 8: 黑名单目录不存在时仍命中 ──


class TestNonexistentBlacklistPaths:
    def test_read_nonexistent_aws_dir(self):
        """~/.aws 可能不存在，但仍应拦截读取"""
        p = str(Path.home() / ".aws" / "credentials")
        # 确认路径确实不存在（或忽略这一步，重点是测试逻辑不依赖文件存在）
        assert is_read_denied(p) is not None

    def test_write_nonexistent_azure_dir(self):
        """~/.azure 可能不存在，但仍应拦截写入"""
        p = str(Path.home() / ".azure" / "some_credential")
        assert is_write_denied(p) is not None

    def test_read_nonexistent_gcloud_dir(self):
        p = str(Path.home() / ".config" / "gcloud" / "access_tokens.db")
        assert is_read_denied(p) is not None
