"""General Manager chat integration."""

from general_manager.chat.bootstrap import initialize_chat
from general_manager.chat.settings import ChatConfigurationError

__all__ = ["ChatConfigurationError", "initialize_chat"]
