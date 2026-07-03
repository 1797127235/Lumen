"""测试 channels/config_store.py 的配置读写和迁移。"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from channels.config_store import (
    add_channel_config,
    load_channel_configs,
    migrate_legacy_channel_config,
    remove_channel_config,
    save_channel_configs,
    update_channel_config,
)
from channels.models import ChannelConfig


@pytest.fixture
def temp_config_dir():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with patch("core.config.USER_DATA_DIR", tmp_path):
            yield tmp_path


def test_load_save_channel_configs(temp_config_dir: Path):
    configs = [
        ChannelConfig(name="web", provider_type="web", enabled=True, config={}),
        ChannelConfig(name="tg", provider_type="telegram", enabled=True, config={"bot_token": "x"}),
    ]
    save_channel_configs(configs)

    loaded = load_channel_configs()
    assert len(loaded) == 2
    assert loaded[0].name == "web"
    assert loaded[1].config["bot_token"] == "x"


def test_add_channel_config(temp_config_dir: Path):
    add_channel_config(ChannelConfig(name="web", provider_type="web"))
    add_channel_config(ChannelConfig(name="web", provider_type="web", config={"foo": "bar"}))

    loaded = load_channel_configs()
    assert len(loaded) == 1
    assert loaded[0].config == {"foo": "bar"}


def test_update_channel_config(temp_config_dir: Path):
    save_channel_configs([ChannelConfig(name="web", provider_type="web", enabled=True)])

    updated = update_channel_config("web", {"enabled": False})
    assert updated is not None
    assert updated.enabled is False

    loaded = load_channel_configs()
    assert loaded[0].enabled is False


def test_remove_channel_config(temp_config_dir: Path):
    save_channel_configs([ChannelConfig(name="web", provider_type="web")])

    assert remove_channel_config("web") is True
    assert remove_channel_config("web") is False
    assert load_channel_configs() == []


def test_migrate_legacy_channel_config_creates_web_and_telegram(temp_config_dir: Path):
    with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "tg-token"}, clear=False):
        migrated = migrate_legacy_channel_config()

    assert migrated is True
    loaded = load_channel_configs()
    names = {c.name for c in loaded}
    assert "web" in names
    assert "telegram" in names

    tg = next(c for c in loaded if c.name == "telegram")
    assert tg.config["bot_token"] == "tg-token"


def test_migrate_legacy_channel_config_respects_lumen_enable_web_0(temp_config_dir: Path):
    with patch.dict("os.environ", {"LUMEN_ENABLE_WEB": "0"}, clear=False):
        migrated = migrate_legacy_channel_config()

    assert migrated is True
    web = next(c for c in load_channel_configs() if c.name == "web")
    assert web.enabled is False


def test_migrate_legacy_channel_config_noop_when_channels_exist(temp_config_dir: Path):
    save_channel_configs([ChannelConfig(name="web", provider_type="web")])

    with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "tg-token"}, clear=False):
        migrated = migrate_legacy_channel_config()

    assert migrated is False
    assert len(load_channel_configs()) == 1
