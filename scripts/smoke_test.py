"""動作確認。サーバー接続 → モデル確認 → 1問だけ投げて速度を測る。

    python scripts/smoke_test.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from localai import LocalAI, ensure_model, is_server_up, list_models, settings  # noqa: E402


def main() -> int:
    print(f"Ollama: {settings.host}")
    if not is_server_up():
        print("NG: サーバーに接続できません。Ollama を起動してください。")
        return 1
    print("OK: サーバー応答あり")
    print(f"モデル一覧: {list_models() or 'なし'}")

    model = ensure_model(settings.model)
    ai = LocalAI(model=model, temperature=0.2)
    q = "日本の都道府県を3つ、カンマ区切りで答えてください。"
    print(f"\nQ: {q}")
    t0 = time.perf_counter()
    first = None
    n = 0
    print("A: ", end="", flush=True)
    for piece in ai.ask_stream(q):
        if first is None:
            first = time.perf_counter() - t0
        n += len(piece)
        print(piece, end="", flush=True)
    total = time.perf_counter() - t0
    gen = max(total - (first or 0), 1e-6)
    print(f"\n\n初回応答まで {first:.1f}s / 全体 {total:.1f}s / 生成 約 {n/gen:.0f} 文字/秒")
    print("（初回応答が数十秒かかるのは最初のモデル読み込みのため。2回目以降は速くなります）")
    print("OK: 動作確認完了")
    return 0


if __name__ == "__main__":
    sys.exit(main())
