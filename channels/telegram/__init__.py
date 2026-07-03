"""Telegram channel compatibility shim — re-exports the built-in provider."""

from __future__ import annotations

from channels.builtins.telegram import TelegramChannelProvider
from channels.builtins.telegram.channel import TelegramChannel

__all__ = ["TelegramChannel", "TelegramChannelProvider"]
