"""ローカルLLM（Ollama）を扱う薄いラッパー。

使い方:
    from localai import chat, chat_stream
    print(chat("こんにちは"))
"""
from .client import (
    LocalAI,
    chat,
    chat_stream,
    ensure_model,
    is_server_up,
    list_models,
    strip_think,
)
from .config import settings

__all__ = [
    "LocalAI",
    "chat",
    "chat_stream",
    "ensure_model",
    "is_server_up",
    "list_models",
    "strip_think",
    "settings",
]
