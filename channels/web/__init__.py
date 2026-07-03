"""Web channel compatibility shim — re-exports the built-in provider."""

from __future__ import annotations

from channels.builtins.web import WebChannelProvider
from channels.builtins.web.web import WebChannel

__all__ = ["WebChannel", "WebChannelProvider"]
