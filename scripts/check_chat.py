"""動いているサーバーの避難者チャット（/api/chat）に質問を投げ、答えと速さを確かめる。

    .venv\\Scripts\\python.exe scripts\\check_chat.py [http://localhost:8000] [--no-notice | --notice-only] [--backend gemini]

--backend gemini: クラウド AI（Gemini）で答えさせる（サーバーに GEMINI_API_KEY が必要。無ければローカルで答える）
"""
import json
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
_argv = sys.argv[1:]
BACKEND = "local"
if "--backend" in _argv:
    i = _argv.index("--backend")
    BACKEND = _argv[i + 1] if i + 1 < len(_argv) else "local"
    del _argv[i:i + 2]
_args = [a for a in _argv if not a.startswith("--")]
BASE = _args[0] if _args else "http://localhost:8000"

QUESTIONS = [
    ("ja", "トイレはどこにありますか？"),
    ("ja", "夜は何時に消灯ですか？"),
    ("ja", "AED はどこ？"),
    ("ja", "ペットを連れてきてもいい？"),
    ("ja", "駐車場はありますか？"),
    ("ja", "明日の天気は？"),
    ("en", "Where can I charge my phone?"),
    ("en", "What time is lights out?"),
    ("en", "Can I bring my dog?"),
    ("en", "Is there a parking lot?"),
]


def ask(lang: str, q: str) -> tuple[str, dict, float | None, float, float | None]:
    """(答え, done イベント, 最初の文字までの秒, 全体の秒, 考え終わるまでの秒)"""
    t = time.time()
    body = json.dumps({"session": "check", "lang": lang, "message": q, "history": [],
                       "backend": BACKEND}).encode()
    req = urllib.request.Request(f"{BASE}/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    out, last, first, thought = "", {}, None, None
    with urllib.request.urlopen(req, timeout=600) as r:
        for line in r:
            line = line.decode().strip()
            if not line.startswith("data:"):
                continue
            d = json.loads(line[5:])
            if "delta" in d:
                first = first or time.time() - t
                out += d["delta"]
            elif d.get("phase") == "write":
                thought = time.time() - t
            elif d.get("done"):
                last = d
    return out.strip(), last, first, time.time() - t, thought


# お知らせを根拠にする質問（テスト用のお知らせを1件入れて、終わったら消す）
NOTICE_QUESTIONS = [
    ("ja", "次の配給はいつ？"),
    ("ja", "おにぎりはどこでもらえる？"),
    ("en", "When is the next food distribution?"),
]


def run(questions):
    for lang, q in questions:
        a, last, first, total, thought = ask(lang, q)
        mode = "FAQ" if last.get("faq") else f"AI:{last.get('backend', '?')}"
        src = [s.get("hhmm") for s in last.get("sources", []) if s.get("type") == "notice"]
        print(f"\n[{lang}] {q}  ({mode}"
              f"{f' / 考え {thought:.1f}s' if thought else ''} / 最初 {first or -1:.1f}s / 全体 {total:.1f}s"
              f"{' / 出典 ' + ','.join(src) if src else ''})\n  → {a}")


if "--notice-only" not in sys.argv:
    run(QUESTIONS)

if "--no-notice" not in sys.argv:
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
    from shelter import db, notices  # noqa: E402
    db.init_db()
    gid = notices.post("food", "昼食の配給", "12:00 から体育館入口で、おにぎりと水を配ります。1人2個まで。",
                       author="check_chat")
    notices.save_translation(gid, "en", "Lunch distribution",
                             "From 12:00 at the gym entrance, rice balls and water. Up to 2 per person.")
    try:
        print("\n--- テスト用のお知らせを入れて確認 ---")
        run(NOTICE_QUESTIONS)
    finally:
        notices.delete_group(gid)
