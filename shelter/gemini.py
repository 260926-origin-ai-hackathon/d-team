"""クラウド AI（Google Gemini API）の呼び出し。

- API キーは .env の GEMINI_API_KEY（DB には入れない）。キーが無ければ available() が False
- messages は Ollama と同じ形（system / user / assistant）で受け取り、Gemini の形に直す
- 埋め込みは扱わない（マニュアル検索・根拠探しは Gemini モードでも Ollama の bge-m3）
"""
from __future__ import annotations

import time
from typing import AsyncIterator

from .config import config

_TIMEOUT_MS = 60_000
_client_cache: dict = {}


def available() -> bool:
    return bool(config.gemini_api_key)


def client():
    """キーごとに1つだけ作って使い回す。google-genai は必要になってから読み込む。"""
    from google import genai
    from google.genai import types

    key = config.gemini_api_key
    if not key:
        raise RuntimeError("GEMINI_API_KEY が設定されていません（.env に書いてください）")
    c = _client_cache.get(key)
    if c is None:
        _client_cache.clear()
        c = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=_TIMEOUT_MS))
        _client_cache[key] = c
    return c


def to_contents(messages: list[dict]) -> tuple[str | None, list]:
    """Ollama 形式の messages → (system_instruction, contents)。assistant は model に読み替える。"""
    from google.genai import types

    system: list[str] = []
    contents: list = []
    for m in messages:
        role, text = m.get("role"), m.get("content") or ""
        if role == "system":
            system.append(text)
            continue
        if not text:
            continue
        contents.append(types.Content(role="model" if role == "assistant" else "user",
                                      parts=[types.Part(text=text)]))
    return ("\n\n".join(system) or None), contents


def _config(system: str | None, max_tokens: int, temperature: float | None):
    from google.genai import types

    return types.GenerateContentConfig(
        system_instruction=system,
        temperature=config.temperature if temperature is None else temperature,
        max_output_tokens=max_tokens,
        thinking_config=types.ThinkingConfig(thinking_budget=config.gemini_thinking_budget),
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),  # 道具は使わない
    )


async def stream(
    messages: list[dict], max_tokens: int, temperature: float | None = None
) -> AsyncIterator[str]:
    """Gemini でストリーミング生成。空のかけらは捨てる。"""
    system, contents = to_contents(messages)
    resp = await client().aio.models.generate_content_stream(
        model=config.gemini_model, contents=contents,
        config=_config(system, max_tokens, temperature))
    async for chunk in resp:
        text = chunk.text
        if text:
            yield text


async def ping() -> dict:
    """接続テスト（設定画面・scripts/check_gemini.py 用）。"""
    if not available():
        return {"ok": False, "model": config.gemini_model,
                "error": "GEMINI_API_KEY が設定されていません"}
    t0 = time.perf_counter()
    try:
        parts = [p async for p in stream(
            [{"role": "user", "content": "「はい」とだけ答えて。"}], max_tokens=16, temperature=0)]
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "model": config.gemini_model, "error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "model": config.gemini_model, "reply": "".join(parts).strip(),
            "latency_ms": round((time.perf_counter() - t0) * 1000)}
