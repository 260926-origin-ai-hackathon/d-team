"""デモ用のサンプル避難者・相談・お知らせを入れる（AI は使わない。英語版も手書き）。

    python scripts/seed_demo.py            # 追加する（同じ人は二重登録にならない。相談も同じ文は二重に入れない）
    python scripts/seed_demo.py --reset    # 名簿・対応メモ・伝えたこと・お知らせ・チャット記録・送信ログを消してから入れる

別の DB に入れるときは環境変数 SHELTER_DB（shelter/config.py）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shelter import cases, checkin, db, notices, voices  # noqa: E402
from shelter.config import now  # noqa: E402

# (氏名, ふりがな, 住所, 性別, 生年月日, 同行人数, 要配慮, アレルギー, メモ, 言語, ペット)
PEOPLE = [
    ("田中 一郎", "たなか いちろう", "東野田町2-3-1", "M", "1942-05-10", 2, ["NUR"], [], "妻が要介護2。夜間トイレ介助", "ja", 0),
    ("鈴木 美咲", "すずき みさき", "東野田町3-8-12", "F", "1993-02-14", 3, ["INF", "PRG"], [], "妊娠7か月・2歳児連れ", "ja", 0),
    ("高橋 健", "たかはし けん", "網島町6-1", "M", "1975-09-30", 4, [], [], "", "ja", 1),
    ("伊藤 さくら", "いとう さくら", "東野田町1-15-2", "F", "1988-07-07", 2, ["ALG"], ["wheat", "egg"], "子どもが小麦・卵アレルギー", "ja", 0),
    ("渡辺 正", "わたなべ ただし", "都島中通2-1-9", "M", "1950-12-01", 1, ["MED"], [], "在宅酸素。ボンベ残り1日", "ja", 0),
    ("Nguyen Van An", "", "Higashinoda 4-2-5", "M", "1998-03-21", 1, [], [], "技能実習生。日本語少し", "en", 0),
    ("山本 和子", "やまもと かずこ", "東野田町5-4-3", "F", "1936-08-18", 1, ["NUR", "DIS"], [], "車いす。耳が遠い", "ja", 0),
    ("中村 直樹", "なかむら なおき", "東野田町2-9-7", "M", "1982-04-04", 3, ["ILL"], [], "糖尿病。インスリン", "ja", 0),
    ("Sarah Johnson", "", "Kyobashi hotel (tourist)", "F", "1991-10-10", 2, [], ["shrimp", "crab"], "Tourist, no Japanese", "en", 0),
    ("小林 陽子", "こばやし ようこ", "東野田町4-11-6", "F", "1965-01-25", 2, [], [], "", "ja", 2),
    ("加藤 大輔", "かとう だいすけ", "都島本通1-2-3", "M", "2001-06-12", 1, [], [], "", "ja", 0),
    ("吉田 恵", "よしだ めぐみ", "東野田町3-3-3", "F", "1979-11-03", 4, ["INF"], ["milk"], "0歳児。ミルクアレルギー用ミルク必要", "ja", 0),
]

# 自己登録の「運営に伝えたいこと」（standard_form.NEEDS のキー）。スマホで登録した人の分
NEEDS_BY_NAME = {
    "田中 一郎": ["mobility", "medicine"],
    "鈴木 美咲": ["baby", "doctor"],
    "伊藤 さくら": ["diet"],
    "渡辺 正": ["oxygen", "medicine"],
    "Nguyen Van An": ["language", "family"],
    "山本 和子": ["mobility", "sleep"],
    "中村 直樹": ["medicine"],
    "Sarah Johnson": ["language", "diet"],
    "吉田 恵": ["baby", "diet"],
}

# 避難者の声（チャットの相談）。(氏名 or None＝匿名端末, 言語, 本文, ガードに当たったときの話題 or None)
# AI は通さず chat_logs に直接入れる。種類・話題は voices.classify（ガードは emergency・outcome=guard。voices.record と同じ）
CHATS = [
    ("渡辺 正", "ja", "急に胸が苦しい", "medical"),                     # 緊急の相談（至急）
    (None, "ja", "夜、体育館の照明を少し暗くしてほしい", None),          # 匿名の要望（様子見）
    ("鈴木 美咲", "ja", "つわりがひどくて体調が悪い", None),            # 医療の困りごと（今日中）
]

# 避難所に伝える（cases）。(氏名, 用件, 急ぐ, 本文, 言語)。同じ本文は二重に入れない
CASES = [
    ("渡辺 正", "medical", True, "血圧の薬があと 1 日分", "ja"),
    ("鈴木 美咲", "baby", False, "粉ミルクが今夜分で無くなる", "ja"),
    ("Nguyen Van An", "family", False, "I cannot reach my wife", "en"),
]

NOTICES = [
    ("water", "08:30", "給水のお知らせ", "9:00〜11:00 に校庭で給水車が来ます。ポリタンクかペットボトルを持ってきてください。",
     "Water supply", "A water truck will be in the schoolyard from 9:00 to 11:00. Bring a tank or plastic bottles."),
    ("toilet", "10:00", "トイレの使い方", "断水中のため、体育館北側のトイレは便器に袋をかぶせて使います。使った袋は外の黒いゴミ箱へ。",
     "How to use the toilets", "Water is out. Put a bag over the toilet in the north toilets of the gym. Put used bags in the black bins outside."),
    ("medical", "12:30", "保健師の巡回", "14:00〜16:00 に保健師が体育館を回ります。薬が足りない人、体調が悪い人は声をかけてください。",
     "Public health nurse visit", "A public health nurse will walk around the gym from 14:00 to 16:00. Talk to them if you are short of medicine or feel sick."),
    ("food", "14:00", "夕食の配給", "15:00 から体育館前で配給します。お皿と箸を持ってきてください。アレルギーのある人は受付で名前を言って対応食を受け取ってください。",
     "Dinner distribution", "Dinner will be handed out in front of the gym from 15:00. Bring a plate and chopsticks. People with allergies: tell your name at the reception to get allergy-friendly food."),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()
    db.init_db()
    if args.reset:
        db.reset_records((*db.RESET_TABLES, "notices"))
        print("名簿・対応メモ・伝えたこと・お知らせ・チャット記録・送信ログを消しました")

    ids: dict[str, int] = {}
    for name, kana, addr, sex, dob, hh, care, alg, note, lang, pet in PEOPLE:
        p = checkin.normalize({"name": name, "kana": kana, "addr": "大阪市都島区" + addr if lang == "ja" else addr,
                               "sex": sex, "dob": dob, "hh": hh, "care": care, "alg": alg, "note": note,
                               "lang": lang, "pet": pet, "needs": NEEDS_BY_NAME.get(name, [])}, require_dev=False)
        status, ev = checkin.register(p, "manual")
        ids[name] = ev["id"]
        print(f"  受付 {ev['id']:>3} {status:9} {name}")

    for name, lang, text, guard in CHATS:
        if db.query_one("SELECT id FROM chat_logs WHERE role='user' AND content=?", (text,)):
            continue
        kind, topic = voices.classify(text, lang)
        outcome = None
        if guard:  # 急病・暴力のガードに当たった発言（main.api_chat と同じ付け方）
            kind, topic, outcome = "emergency", guard, "guard"
        eid = ids.get(name) if name else None
        db.execute(
            "INSERT INTO chat_logs(session, lang, role, content, created_at, kind, topic, status, outcome, evacuee_id)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (f"seed-{eid or 'anon'}", lang, "user", text, now().isoformat(), kind, topic, "open", outcome, eid))
        print(f"  相談 {name or '匿名':12} [{voices.KINDS[kind]}／{voices.TOPIC_LABEL[topic]}] {text}")

    for name, cat, urgent, text, lang in CASES:
        if db.query_one("SELECT id FROM cases WHERE text=?", (text,)):
            continue
        cid = cases.create(ids[name], cat, urgent, text, lang)
        print(f"  伝える {cid:>3} {name:12} [{cases.CATEGORIES[cat]}{' 急ぎ' if urgent else ''}] {text}")

    today = now().strftime("%Y-%m-%d")
    existing = {n["title"] for n in notices.list_notices("ja", limit=200)}
    for cat, hm, title, body, en_t, en_b in NOTICES:
        if title in existing:
            continue
        gid = notices.post(cat, title, body, f"{today}T{hm}:00+09:00")
        notices.save_translation(gid, "en", en_t, en_b)
        print(f"  お知らせ {hm} {title}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
