"""测试 lib/memory/loader.py 的插件发现与加载。"""

from __future__ import annotations

import tempfile
from pathlib import Path

from lib.memory.loader import (
    _discover_in_directory,
    discover_builtin_providers,
    discover_providers,
    discover_user_providers,
    load_provider,
)
from lib.memory.provider import MemoryProvider

_FAKE_PROVIDER_PY = """
from lib.memory.provider import MemoryProvider

class Provider(MemoryProvider):
    def __init__(self, value: str = "default"):
        self.value = value

    @property
    def name(self):
        return "fake"

    async def is_available(self):
        return True

    async def initialize(self, session_id, **kwargs):
        pass

    async def get_tool_schemas(self):
        return []
"""


_USER_OVERRIDE_PY = """
from lib.memory.provider import MemoryProvider

class Provider(MemoryProvider):
    def __init__(self, value: str = "default"):
        self.value = value

    @property
    def name(self):
        return "fake"

    async def is_available(self):
        return True

    async def initialize(self, session_id, **kwargs):
        pass

    async def get_tool_schemas(self):
        return []
"""


def _write_plugin(directory: Path, name: str, plugin_py: str, class_name: str = "Provider") -> None:
    plugin_dir = directory / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "__init__.py").write_text(plugin_py, encoding="utf-8")
    (plugin_dir / "plugin.yaml").write_text(
        f'name: {name}\nversion: "0.0.1"\nclass: {class_name}\n',
        encoding="utf-8",
    )


def test_discover_in_directory_finds_plugin():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        _write_plugin(tmp_path, "fake", _FAKE_PROVIDER_PY)

        result = _discover_in_directory(tmp_path)
        assert "fake" in result
        assert issubclass(result["fake"], MemoryProvider)


def test_discover_providers_merges_builtin_and_user():
    with tempfile.TemporaryDirectory() as builtin_tmp, tempfile.TemporaryDirectory() as user_tmp:
        builtin_path = Path(builtin_tmp)
        user_path = Path(user_tmp)

        _write_plugin(builtin_path, "builtin_fake", _FAKE_PROVIDER_PY)
        _write_plugin(user_path, "user_fake", _FAKE_PROVIDER_PY)

        result = discover_providers(builtin_dir=builtin_path, plugins_dir=user_path)
        assert "builtin_fake" in result
        assert "user_fake" in result


def test_user_plugin_overrides_builtin():
    with tempfile.TemporaryDirectory() as builtin_tmp, tempfile.TemporaryDirectory() as user_tmp:
        builtin_path = Path(builtin_tmp)
        user_path = Path(user_tmp)

        _write_plugin(builtin_path, "fake", _FAKE_PROVIDER_PY)
        _write_plugin(user_path, "fake", _USER_OVERRIDE_PY.replace('return "fake"', 'return "fake-user"'))

        result = discover_providers(builtin_dir=builtin_path, plugins_dir=user_path)
        assert "fake" in result
        # 用户目录覆盖，实例化后 name 为 fake-user
        instance = result["fake"](value="x")
        assert instance.name == "fake-user"


def test_load_provider_passes_config():
    with tempfile.TemporaryDirectory() as builtin_tmp:
        builtin_path = Path(builtin_tmp)
        _write_plugin(builtin_path, "fake", _FAKE_PROVIDER_PY)

        provider = load_provider(
            "fake", config={"value": "configured"}, builtin_dir=builtin_path, plugins_dir=builtin_path
        )
        assert provider is not None
        assert provider.value == "configured"


def test_load_provider_missing_returns_none():
    with tempfile.TemporaryDirectory() as builtin_tmp:
        builtin_path = Path(builtin_tmp)
        provider = load_provider("nonexistent", builtin_dir=builtin_path, plugins_dir=builtin_path)
        assert provider is None


def test_discover_builtin_and_user_separately():
    with tempfile.TemporaryDirectory() as builtin_tmp, tempfile.TemporaryDirectory() as user_tmp:
        builtin_path = Path(builtin_tmp)
        user_path = Path(user_tmp)

        _write_plugin(builtin_path, "only_builtin", _FAKE_PROVIDER_PY)
        _write_plugin(user_path, "only_user", _FAKE_PROVIDER_PY)

        builtin = discover_builtin_providers(builtin_dir=builtin_path)
        user = discover_user_providers(plugins_dir=user_path)

        assert "only_builtin" in builtin
        assert "only_builtin" not in user
        assert "only_user" in user
        assert "only_user" not in builtin
