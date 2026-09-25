"""自由文から構造化データ（JSON）を抜き出す（例: 問い合わせメール → 項目）。

    python examples/extract_json.py "山田です。来週火曜14時にA社との打合せをお願いします。参加は3名です。"

Ollama の JSON スキーマ指定（format）を使うので、必ず妥当な JSON が返ります。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ollama  # noqa: E402

from localai import ensure_model, settings, strip_think  # noqa: E402

SCHEMA = {
    "type": "object",
    "properties": {
        "sender": {"type": "string", "description": "差出人の名前"},
        "request": {"type": "string", "description": "依頼内容の要約"},
        "datetime": {"type": "string", "description": "日時（本文の表現のまま。無ければ空）"},
        "participants": {"type": "integer", "description": "参加人数（不明なら0）"},
        "priority": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["sender", "request", "datetime", "participants", "priority"],
}


def extract(text: str, model: str | None = None) -> dict:
    model = ensure_model(model)
    res = ollama.Client(host=settings.host).chat(
        model=model,
        messages=[
            {"role": "system", "content": "本文から情報を抽出し、指定のJSONで返してください。本文に無い情報は捏造しないでください。"},
            {"role": "user", "content": text},
        ],
        format=SCHEMA,
        options={"temperature": 0},
        think=False,
    )
    return json.loads(strip_think(res.message.content or "{}"))


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    print(json.dumps(extract(" ".join(sys.argv[1:])), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
