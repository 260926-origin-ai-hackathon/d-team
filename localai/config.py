"""設定。.env → 環境変数 → 既定値 の順で読む。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# リポジトリ直下の .env を読む（無ければ何もしない）
_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / ".env")

DEFAULT_MODEL = "qwen3.5:4b"
DEFAULT_HOST = "http://localhost:11434"
DEFAULT_SYSTEM_PROMPT = "あなたは日本語で簡潔に答えるビジネスアシスタントです。"

# 低スペックPC向けの代替候補（軽い順）
FALLBACK_MODELS = ["gemma3:1b", "qwen3.5:2b", "qwen3.5:4b"]


@dataclass
class Settings:
    model: str = os.getenv("LOCALAI_MODEL", DEFAULT_MODEL)
    host: str = os.getenv("OLLAMA_HOST", DEFAULT_HOST)
    system_prompt: str = os.getenv("LOCALAI_SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT)
    # 生成パラメータ
    temperature: float = float(os.getenv("LOCALAI_TEMPERATURE", "0.7"))
    num_ctx: int = int(os.getenv("LOCALAI_NUM_CTX", "8192"))


settings = Settings()
