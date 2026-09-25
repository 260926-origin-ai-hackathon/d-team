"""AI 呼び出し（Ollama の直列化・待ち人数・ウォームアップ・「考えてから生成」／Gemini への振り分け）。

- チャット生成は asyncio.Lock で1本ずつ（PC1台で同時に回すと全員が遅くなるため）
- keep_alive=-1 でモデルをメモリに留める
- 接続先・既定モデルは localai の設定（.env の LOCALAI_MODEL / OLLAMA_HOST）を使う
- Plan を渡すと、同じ順番の中で「短い考え → 答え」の2段階で生成する（避難者チャットが使う）
- backend="gemini" のときは生成だけクラウド（shelter/gemini.py）に回す。ロック・整理券は使わない。
  2段階（Plan）はどちらでも同じ。埋め込み（embed）はどちらのモードでも Ollama
"""
from __future__ import annotations

import asyncio
import itertools
import logging
import time
from dataclasses import dataclass
from typing import AsyncIterator, Callable

import ollama

from localai import strip_think

from . import gemini
from .config import config

log = logging.getLogger("shelter.llm")

_lock = asyncio.Lock()  # asyncio.Lock は待った順（FIFO）に起こす
_tickets = itertools.count(1)
_waiting: list[int] = []
_state = {"busy": False, "warm": False, "last_error": "", "last_tps": None,
          "gemini_last_error": ""}

BACKENDS = ("local", "gemini")


def pick(requested: str | None, force_local: bool = False) -> str:
    """画面から来た backend を決める。Gemini はキーがあるときだけ。それ以外はすべて local。"""
    if not force_local and requested == "gemini" and gemini.available():
        return "gemini"
    return "local"


def _client() -> ollama.AsyncClient:
    return ollama.AsyncClient(host=config.ollama_host)


def status() -> dict:
    return {
        "model": config.chat_model,
        "embed_model": config.embed_model,
        "warm": _state["warm"],
        "queue": len(_waiting) + (1 if _state["busy"] else 0),
        "last_error": _state["last_error"],
        "tokens_per_sec": _state["last_tps"],
        "gemini": {"available": gemini.available(), "model": config.gemini_model,
                   "last_error": _state["gemini_last_error"]},
    }


def position(ticket: int) -> int:
    """その番号の前に何人いるか（生成中の1人を含む）。0 なら自分の番。"""
    if ticket not in _waiting:
        return 0
    return sum(1 for t in _waiting if t < ticket) + (1 if _state["busy"] else 0)


@dataclass
class Plan:
    """「考えてから生成する」の設定。stream() が同じ順番（ロック）の中で、まず短い考えを作ってから答えを生成する。

    - max_tokens: 考えの長さの上限
    - followup(考え): 答えを生成するための messages を返す。str を返したら生成せず、その文をそのまま答えにする
    考えは meta["plan"] に、いまの段階（"think" → "write"）は meta["phase"] に入る。
    """
    max_tokens: int
    followup: Callable[[str], list[dict] | str]
    temperature: float = 0.0  # 考えは毎回同じになるよう温度 0（根拠の番号選びを揺らさない）


def _options(max_tokens: int, temperature: float | None) -> dict:
    return {
        "temperature": config.temperature if temperature is None else temperature,
        "num_ctx": config.num_ctx,
        "num_predict": max_tokens,
    }


async def _generate(
    messages: list[dict], max_tokens: int, temperature: float | None
) -> AsyncIterator[str]:
    """Ollama を1回呼んで断片を返す（ロックは呼ぶ側が持つ）。"""
    in_think = False
    resp = await _client().chat(
        model=config.chat_model,
        messages=messages,
        options=_options(max_tokens, temperature),
        stream=True,
        think=False,
        keep_alive=-1,
    )
    async for part in resp:
        piece = part.message.content or ""
        if not piece:
            continue
        # think=False でも <think> を出すモデル対策（ブロックごと捨てる）
        if "<think>" in piece:
            in_think = True
            piece = piece.split("<think>", 1)[0]
        if in_think:
            if "</think>" in piece:
                in_think = False
                piece = piece.split("</think>", 1)[1].lstrip()
            else:
                continue
        if piece:
            yield piece


async def _two_stage(
    gen: Callable[[list[dict], int, float | None], AsyncIterator[str]],
    messages: list[dict], max_tokens: int, temperature: float | None,
    meta: dict, plan: Plan | None,
) -> AsyncIterator[str]:
    """plan があれば「考え → 答え」、無ければそのまま1回生成する。gen は1回分の生成（Ollama か Gemini）。"""
    if plan is not None:
        meta["phase"] = "think"
        parts = [p async for p in gen(messages, plan.max_tokens, plan.temperature)]
        thought = strip_think("".join(parts)).strip()
        meta["plan"] = thought
        nxt = plan.followup(thought)
        if isinstance(nxt, str):  # 生成せずに決まった文を返す
            meta["phase"] = "write"
            yield nxt
            return
        messages = nxt
    meta["phase"] = "write"
    async for piece in gen(messages, max_tokens, temperature):
        yield piece


async def stream(
    messages: list[dict], max_tokens: int, temperature: float | None = None,
    meta: dict | None = None, backend: str = "local", plan: Plan | None = None,
) -> AsyncIterator[str]:
    """チャットをストリーミングで生成。

    local: Ollama で直列。meta を渡すと meta["ticket"] に整理券番号が入る。
    gemini: クラウドで並列（整理券なし）。失敗しても local には落とさず例外を投げる。

    plan を渡すと、まず plan.max_tokens 以内の「考え」を作り（画面には出さない）、
    plan.followup(考え) が返した messages で答えを生成する。1段階目の会話に考えを足した messages を
    返せば、前置き〜根拠までを Ollama が使い回すので 2 段階目の読み直しはほぼ無い。
    """
    if meta is None:
        meta = {}
    if backend == "gemini":
        async for piece in _stream_gemini(messages, max_tokens, temperature, meta, plan):
            yield piece
        return
    async for piece in _stream_local(messages, max_tokens, temperature, meta, plan):
        yield piece


async def _stream_gemini(
    messages: list[dict], max_tokens: int, temperature: float | None,
    meta: dict, plan: Plan | None,
) -> AsyncIterator[str]:
    try:
        async for piece in _two_stage(gemini.stream, messages, max_tokens, temperature, meta, plan):
            yield piece
        _state["gemini_last_error"] = ""
    except Exception as e:  # noqa: BLE001
        _state["gemini_last_error"] = f"{type(e).__name__}: {e}"[:300]
        log.warning("gemini failed: %s", _state["gemini_last_error"])
        raise


async def _stream_local(
    messages: list[dict], max_tokens: int, temperature: float | None,
    meta: dict, plan: Plan | None,
) -> AsyncIterator[str]:
    ticket = next(_tickets)
    meta["ticket"] = ticket
    _waiting.append(ticket)
    try:
        await _lock.acquire()
    finally:
        _waiting.remove(ticket)
    _state["busy"] = True
    t0, n = time.perf_counter(), 0

    async def gen(msgs: list[dict], mt: int, temp: float | None) -> AsyncIterator[str]:
        nonlocal n
        async for piece in _generate(msgs, mt, temp):
            n += 1
            yield piece

    try:
        async for piece in _two_stage(gen, messages, max_tokens, temperature, meta, plan):
            yield piece
        _state["warm"] = True
        _state["last_error"] = ""
        dt = time.perf_counter() - t0
        if dt > 0 and n:
            _state["last_tps"] = round(n / dt, 1)
    except Exception as e:  # noqa: BLE001
        _state["last_error"] = f"{type(e).__name__}: {e}"
        raise
    finally:
        _state["busy"] = False
        _lock.release()


async def complete(
    messages: list[dict], max_tokens: int, temperature: float | None = None,
    backend: str = "local",
) -> str:
    parts = [p async for p in stream(messages, max_tokens, temperature, backend=backend)]
    return strip_think("".join(parts))


async def embed(texts: list[str]) -> list[list[float]]:
    """埋め込み（生成とは別に軽いので直列化しない）。"""
    res = await _client().embed(model=config.embed_model, input=texts, keep_alive=-1)
    return [list(v) for v in res.embeddings]


async def warmup() -> None:
    """起動時に1問投げてモデルを載せる。失敗しても起動は続ける。"""
    try:
        await complete([{"role": "user", "content": "「はい」とだけ答えて。"}], max_tokens=8)
        log.info("chat model warm: %s", config.chat_model)
    except Exception as e:  # noqa: BLE001
        log.warning("warmup failed (chat): %s", e)
    try:
        await embed(["ウォームアップ"])
    except Exception as e:  # noqa: BLE001
        log.warning("warmup failed (embed): %s", e)
