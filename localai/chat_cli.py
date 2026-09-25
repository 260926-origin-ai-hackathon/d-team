"""ターミナルで対話するチャット。

    python -m localai.chat_cli
    python -m localai.chat_cli --model gemma3:1b

コマンド: /reset 履歴クリア, /model <名前> 切替, /models 一覧, /quit 終了
"""
from __future__ import annotations

import argparse
import sys

from . import LocalAI, ensure_model, is_server_up, list_models, settings


def main() -> int:
    ap = argparse.ArgumentParser(description="ローカルLLM チャット")
    ap.add_argument("--model", default=settings.model, help="使うモデル名")
    ap.add_argument("--system", default=settings.system_prompt, help="システムプロンプト")
    args = ap.parse_args()

    if not is_server_up():
        print(
            "Ollama サーバーに接続できません。\n"
            "  Ollama を起動してから再実行してください"
            "（スタートメニュー > Ollama、または ollama serve）。",
            file=sys.stderr,
        )
        return 1

    model = ensure_model(args.model)
    ai = LocalAI(model=model, system_prompt=args.system)
    print(f"[model: {model}]  /quit で終了、/reset で履歴クリア")

    while True:
        try:
            text = input("\nあなた> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text in ("/quit", "/exit", "/q"):
            break
        if text == "/reset":
            ai.reset()
            print("(履歴をクリアしました)")
            continue
        if text == "/models":
            print("\n".join(list_models()))
            continue
        if text.startswith("/model "):
            ai.model = ensure_model(text.split(maxsplit=1)[1])
            print(f"(model -> {ai.model})")
            continue

        print("AI> ", end="", flush=True)
        try:
            for piece in ai.ask_stream(text):
                print(piece, end="", flush=True)
        except KeyboardInterrupt:
            print("\n(中断)")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
