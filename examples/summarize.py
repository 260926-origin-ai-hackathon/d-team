"""テキストファイルを要約する（ビジネス用途の例: 議事録・メール・報告書）。

    python examples/summarize.py 議事録.txt
    python examples/summarize.py 議事録.txt --style bullets
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from localai import LocalAI, ensure_model  # noqa: E402

STYLES = {
    "short": "3行以内で要約してください。",
    "bullets": "重要ポイントを箇条書き5点以内で、最後に「次のアクション」を1行で書いてください。",
    "exec": "経営層向けに、結論→理由→リスク の順で各1〜2文でまとめてください。",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file", help="要約するテキストファイル")
    ap.add_argument("--style", choices=STYLES, default="bullets")
    args = ap.parse_args()

    text = Path(args.file).read_text(encoding="utf-8")
    ai = LocalAI(
        model=ensure_model(),
        system_prompt="あなたは日本語のビジネス文書を正確に要約するアシスタントです。推測は書かず、本文にあることだけを書いてください。",
        temperature=0.2,
    )
    prompt = f"{STYLES[args.style]}\n\n--- 本文 ---\n{text}"
    for piece in ai.ask_stream(prompt):
        print(piece, end="", flush=True)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
