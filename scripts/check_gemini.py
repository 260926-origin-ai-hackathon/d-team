"""クラウド AI（Gemini API）につながるかを確かめる。

    .venv\\Scripts\\python.exe scripts\\check_gemini.py

.env の GEMINI_API_KEY を読み、使えるモデル名の一覧と、1往復の時間を表示する。
キーが無ければ「未設定」と出して終わる（このときアプリはローカルの AI だけで動く）。
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from shelter import gemini  # noqa: E402
from shelter.config import config  # noqa: E402


def main() -> int:
    if not gemini.available():
        print("GEMINI_API_KEY: 未設定（.env に GEMINI_API_KEY=... を書くとスイッチが出ます）")
        return 0
    key = config.gemini_api_key
    print(f"GEMINI_API_KEY: 設定済み（{key[:4]}…{key[-2:]}）")
    print(f"使うモデル: {config.gemini_model} / thinking_budget={config.gemini_thinking_budget}")

    try:
        names = [m.name.removeprefix("models/") for m in gemini.client().models.list()
                 if "generateContent" in (getattr(m, "supported_actions", None) or [])]
    except Exception as e:  # noqa: BLE001
        print(f"モデル一覧の取得に失敗: {type(e).__name__}: {e}")
        return 1
    names = sorted(n for n in names if n.startswith("gemini"))
    print(f"generateContent が使えるモデル（{len(names)} 件）:")
    for n in names:
        print(("  * " if n == config.gemini_model else "    ") + n)
    if config.gemini_model not in names:
        print(f"注意: {config.gemini_model} が一覧にありません（GEMINI_MODEL を見直してください）")

    r = asyncio.run(gemini.ping())
    if r["ok"]:
        print(f"ping OK: {r['latency_ms']} ms / 返事 {r['reply']!r}")
        return 0
    print(f"ping 失敗: {r['error']}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
