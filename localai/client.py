"""Ollama クライアントのラッパー。

- ストリーミング / 非ストリーミングのチャット
- 会話履歴の保持
- モデルの自動 pull
- Qwen3 系の <think> ブロック除去
"""
from __future__ import annotations

import re
from typing import Iterator

import ollama

from .config import settings

_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


def _client(host: str | None = None) -> ollama.Client:
    return ollama.Client(host=host or settings.host)


def is_server_up(host: str | None = None) -> bool:
    """Ollama サーバーが応答するか。"""
    try:
        _client(host).list()
        return True
    except Exception:
        return False


def list_models(host: str | None = None) -> list[str]:
    """ローカルにある（pull 済みの）モデル名一覧。"""
    res = _client(host).list()
    return sorted(m.model for m in res.models if m.model)


def ensure_model(model: str | None = None, host: str | None = None) -> str:
    """モデルが無ければ pull する。戻り値は使用するモデル名。"""
    model = model or settings.model
    if model not in list_models(host):
        print(f"[localai] モデル {model} をダウンロードします（初回のみ、数GB）...")
        for prog in _client(host).pull(model, stream=True):
            if prog.total and prog.completed:
                pct = prog.completed * 100 // prog.total
                print(f"\r  {prog.status} {pct}%", end="", flush=True)
        print("\r  完了                      ")
    return model


def strip_think(text: str) -> str:
    """Qwen3 系が出す <think>...</think> を取り除く。"""
    return _THINK_RE.sub("", text).strip()


class LocalAI:
    """会話履歴を持つチャットセッション。

    >>> ai = LocalAI()
    >>> ai.ask("自己紹介して")
    """

    def __init__(
        self,
        model: str | None = None,
        system_prompt: str | None = None,
        host: str | None = None,
        temperature: float | None = None,
        num_ctx: int | None = None,
    ):
        self.model = model or settings.model
        self.host = host or settings.host
        self.system_prompt = (
            settings.system_prompt if system_prompt is None else system_prompt
        )
        self.options = {
            "temperature": settings.temperature if temperature is None else temperature,
            "num_ctx": settings.num_ctx if num_ctx is None else num_ctx,
        }
        self.history: list[dict] = []
        self._cli = _client(self.host)

    # --- 履歴操作 -----------------------------------------------------
    def reset(self) -> None:
        self.history.clear()

    def _messages(self, user_text: str, images: list[str] | None = None) -> list[dict]:
        msgs: list[dict] = []
        if self.system_prompt:
            msgs.append({"role": "system", "content": self.system_prompt})
        msgs.extend(self.history)
        user_msg: dict = {"role": "user", "content": user_text}
        if images:
            user_msg["images"] = images
        msgs.append(user_msg)
        return msgs

    # --- 生成 ---------------------------------------------------------
    def ask(self, user_text: str, images: list[str] | None = None) -> str:
        """1ターン分を送って全文を返す（履歴に追加）。images は画像ファイルパス。"""
        res = self._cli.chat(
            model=self.model,
            messages=self._messages(user_text, images),
            options=self.options,
            think=False,
        )
        text = strip_think(res.message.content or "")
        self.history.append({"role": "user", "content": user_text})
        self.history.append({"role": "assistant", "content": text})
        return text

    def ask_stream(
        self, user_text: str, images: list[str] | None = None
    ) -> Iterator[str]:
        """1ターン分を送ってトークン単位で yield する（履歴に追加）。"""
        chunks: list[str] = []
        stream = self._cli.chat(
            model=self.model,
            messages=self._messages(user_text, images),
            options=self.options,
            stream=True,
            think=False,
        )
        for part in stream:
            piece = part.message.content or ""
            if piece:
                chunks.append(piece)
                yield piece
        full = strip_think("".join(chunks))
        self.history.append({"role": "user", "content": user_text})
        self.history.append({"role": "assistant", "content": full})


# --- 関数版（履歴なし・使い捨て） ------------------------------------
def chat(prompt: str, system_prompt: str | None = None, model: str | None = None) -> str:
    """1回きりの問い合わせ。"""
    return LocalAI(model=model, system_prompt=system_prompt).ask(prompt)


def chat_stream(
    prompt: str, system_prompt: str | None = None, model: str | None = None
) -> Iterator[str]:
    """1回きりの問い合わせ（ストリーミング）。"""
    yield from LocalAI(model=model, system_prompt=system_prompt).ask_stream(prompt)
