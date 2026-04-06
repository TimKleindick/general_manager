"""Built-in chat providers."""

from general_manager.chat.providers.anthropic import AnthropicProvider
from general_manager.chat.providers.google import GeminiProvider, GoogleProvider
from general_manager.chat.providers.ollama import OllamaProvider
from general_manager.chat.providers.openai import OpenAIProvider


PROVIDER_EXTRA = {
    "OllamaProvider": "chat-ollama",
    "OpenAIProvider": "chat-openai",
    "AnthropicProvider": "chat-anthropic",
    "GeminiProvider": "chat-google",
    "GoogleProvider": "chat-google",
}

__all__ = [
    "PROVIDER_EXTRA",
    "AnthropicProvider",
    "GeminiProvider",
    "GoogleProvider",
    "OllamaProvider",
    "OpenAIProvider",
]
