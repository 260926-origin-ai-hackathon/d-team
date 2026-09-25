"""避難者チャットの根拠探しを確かめる（しきい値の調整用）。

    .venv\\Scripts\\python.exe scripts\\check_evidence.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from shelter import evidence  # noqa: E402

QUESTIONS = [
    ("ja", "トイレはどこにありますか？"),
    ("ja", "スマホの充電できる？"),
    ("ja", "夜は何時に消灯ですか？"),
    ("ja", "薬がなくなりそう"),
    ("ja", "ペットを連れてきてもいい？"),
    ("ja", "AED はどこ？"),
    ("ja", "家族を探しています"),
    ("ja", "次の配給はいつ？"),
    ("ja", "駐車場はありますか？"),
    ("ja", "明日の天気は？"),
    ("en", "Where can I charge my phone?"),
    ("en", "What time is lights out?"),
    ("en", "Where is the toilet?"),
    ("en", "Can I bring my dog?"),
    ("en", "Is there a parking lot?"),
]


async def main():
    for lang, q in QUESTIONS:
        r = await evidence.retrieve(q, lang)
        mode = "FAQ" if r["direct"] else "AI"
        print(f"\n[{lang}] {q} → {mode}  words={r['words']}")
        for e in r["evidence"][:3]:
            mark = "*" if e["hit"] else " "
            print(f"   {e['score']:.3f}{mark} {e['kind']:6} {e['text'][:70]}")


asyncio.run(main())
