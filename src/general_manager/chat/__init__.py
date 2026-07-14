"""General Manager chat integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from general_manager.utils.public_api import build_module_dir, resolve_export

__all__ = ["ChatConfigurationError", "initialize_chat"]

_MODULE_MAP = {
    "ChatConfigurationError": (
        "general_manager.chat.settings",
        "ChatConfigurationError",
    ),
    "initialize_chat": ("general_manager.chat.bootstrap", "initialize_chat"),
}

if TYPE_CHECKING:
    from general_manager.chat.bootstrap import initialize_chat
    from general_manager.chat.settings import ChatConfigurationError


def __getattr__(name: str) -> object:
    """Resolve and cache a public chat export."""
    return resolve_export(
        name,
        module_all=__all__,
        module_map=_MODULE_MAP,
        module_globals=globals(),
    )


def __dir__() -> list[str]:
    """Return the chat module's available names."""
    return build_module_dir(module_all=__all__, module_globals=globals())
