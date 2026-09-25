"""避難者の声: 避難者チャットの発言を「種類」と「話題」に分けて数え、運営画面（/staff/voices）に出す。

- 分類は言葉の一致＋埋め込みの近さ（classify_full）。記録時は言葉の一致（一瞬）で付け、答えを返した裏で
  埋め込み（bge-m3）で見直す。言葉の表で決まらない発言だけ、例文（EXAMPLES）のうち一番近いものの種類・話題を採る
  種類 kind: question（質問）／request（要望）／trouble（困りごと・不満）／emergency（暴力・急病のガードに当たった）／other
  話題 topic: TOPICS のキー（配給・水・薬・トイレ・充電…）。当たらなければ other
- 回答の結果 outcome は main.py が答えを返した後に書く: faq（FAQ の答え）／ai（AI が根拠から答えた）／none（「その情報はまだありません」）／guard／error
  → 「答えられなかった質問」が多い話題は、お知らせや案内に載せる候補
- 運営者が「対応済み」にできる（status: open／done）。本文の書き換えはしない
- チャットは匿名（session は端末の乱数）。氏名は入っていない前提（仕様書 §8.1・§11）
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections import Counter, defaultdict
from datetime import timedelta

from . import db, llm
from .config import now

log = logging.getLogger("shelter.voices")

KINDS = {
    "question": "質問",
    "request": "要望",
    "trouble": "困りごと・不満",
    "emergency": "緊急（ガード）",
    "other": "その他",
}
# 話題 → (表示名, お知らせのカテゴリ, 言葉)
TOPICS: dict[str, tuple[str, str, list[str]]] = {
    "food": ("配給・食事", "food", ["配給", "食事", "ご飯", "ごはん", "弁当", "食べ", "朝食", "昼食", "夕食", "おにぎり", "パン",
                                    "food", "meal", "eat", "breakfast", "lunch", "dinner", "hungry"]),
    "water": ("水・給水", "water", ["水", "給水", "飲み物", "お茶", "water", "drink"]),
    "medical": ("薬・体調・医療", "medical", ["薬", "体調", "痛", "熱", "病", "医", "けが", "怪我", "咳", "吐", "下痢", "血圧", "透析",
                                        "妊", "aed", "救急", "かゆ", "発疹", "じんましん", "めまい", "息苦", "出血", "骨折", "倒れ",
                                        "意識", "具合", "寒気", "発作", "しびれ", "脱水", "熱中症", "持病", "血糖",
                                        "medicine", "sick", "doctor", "nurse", "pain", "fever", "hurt", "ill", "pill", "cough",
                                        "vomit", "nausea", "diarrhea", "dizzy", "headache", "rash", "itch", "bleed", "injur",
                                        "allergic", "seizure", "unconscious"]),
    "toilet": ("トイレ", "toilet", ["トイレ", "便所", "toilet", "restroom", "bathroom"]),
    "bath": ("風呂・洗濯・衛生", "other", ["風呂", "入浴", "シャワー", "洗濯", "歯", "石けん", "石鹸", "bath", "shower", "laundry", "wash"]),
    "bedding": ("寝具・寒さ暑さ", "other", ["毛布", "布団", "寝", "眠", "寒", "暑", "暖房", "冷房", "ベッド", "マット",
                                     "blanket", "sleep", "cold", "hot", "heater", "bed"]),
    "env": ("生活環境（騒音・場所・プライバシー）", "other", ["うるさ", "騒", "静か", "狭", "場所", "スペース", "間仕切", "プライバシー", "臭",
                                                "におい", "タバコ", "たばこ", "喫煙", "ゴミ", "ごみ", "掃除", "汚", "noise", "noisy",
                                                "loud", "quiet", "space", "privacy", "smell", "smoke", "dirty", "trash"]),
    "charge": ("充電・電源・通信", "other", ["充電", "電源", "コンセント", "電池", "wifi", "wi-fi", "電波", "ネット", "charge",
                                     "battery", "outlet", "internet", "signal", "phone"]),
    "family": ("家族・安否", "other", ["家族", "安否", "連絡", "探し", "行方", "見つから", "みつから", "はぐれ", "息子", "娘", "夫", "妻",
                                   "母", "父", "祖", "family", "contact", "missing", "find my", "son", "daughter", "wife", "husband"]),
    "baby": ("乳幼児・子ども", "other", ["ミルク", "おむつ", "オムツ", "授乳", "赤ちゃん", "乳児", "子ども", "子供", "こども",
                                   "milk", "diaper", "baby", "child", "kids"]),
    "pet": ("ペット", "other", ["ペット", "犬", "猫", "pet", "dog", "cat"]),
    "safety": ("防犯・不安", "other", ["怖", "不審", "盗", "防犯", "見回り", "夜間", "性被害", "暴力", "痴漢", "つきまと",
                                  "afraid", "scared", "stolen", "theft", "suspicious", "harass", "violence"]),
    "staff": ("運営・対応", "other", ["職員", "スタッフ", "運営", "対応", "受付", "係", "staff", "reception", "office", "volunteer"]),
    "procedure": ("手続き・制度", "other", ["罹災", "証明", "手続", "仮設", "申請", "保険", "給付", "certificate", "procedure",
                                      "apply", "insurance", "housing"]),
    "info": ("災害・交通・情報", "other", ["地震", "余震", "津波", "雨", "台風", "天気", "停電", "断水", "電車", "バス", "道路",
                                     "帰", "自宅", "いつまで", "避難所", "避難場所", "earthquake", "tsunami", "weather", "train",
                                     "bus", "road", "go home", "how long", "shelter", "evacuation"]),
    "facility": ("施設・設備・時間", "other", ["消灯", "駐車", "門限", "開館", "閉館", "何時まで", "入口", "出口", "エレベータ", "階段",
                                        "喫煙所", "体育館", "教室", "lights out", "parking", "curfew", "entrance", "exit",
                                        "elevator", "gym", "open until"]),
}
TOPIC_LABEL = {k: v[0] for k, v in TOPICS.items()} | {"other": "その他"}
TOPIC_CATEGORY = {k: v[1] for k, v in TOPICS.items()} | {"other": "other"}  # お知らせのカテゴリ

ACTIONABLE = ("request", "trouble", "emergency")  # 運営の対応が要る種類（未対応の件数に数える）
OUTCOMES = {"faq": "FAQ で回答", "ai": "AI が回答", "none": "答えられず", "guard": "決まった案内", "error": "エラー"}

_REQUEST = re.compile(
    r"ほしい|欲しい|ください|下さい|してもら|してくれ|もらえ|貸して|お願い|頼み|希望|必要|要り|いります|たい[。！!]?$|たいです|たいの|"
    r"できますか|できませんか|できるか|もらいたい|\b(want|need|please|could you|can you|would like|can i get|can i have|"
    r"is it possible|request|give me|lend)\b",
    re.IGNORECASE)
_TROUBLE = re.compile(
    r"困|ひどい|酷い|うるさ|騒|寒い|暑い|臭|くさい|汚|狭い|足りな|少な|無くな|なくな|切れ|遅い|遅れ|待たされ|まずい|不安|"
    r"つらい|辛い|苦しい|しんどい|眠れ|寝られ|寝れ|痛|悪い|怖|不満|不公平|ずるい|最悪|嫌|イヤ|だめ|ダメ|ない[。！!]?$|ません[。！!]?$|"
    r"できない|わからない|分からない|"
    # 体調・けが（症状を書いた発言は困りごと。「熱が出た」「子どもが吐いた」など）
    r"熱|発熱|咳|せき|吐|嘔吐|下痢|めまい|頭痛|腹痛|息苦|呼吸|出血|血が|けが|怪我|骨折|倒れ|意識|具合|気分が|寒気|震え|"
    r"脱水|熱中症|低体温|かゆ|発疹|じんましん|持病|血圧|血糖|発作|しびれ|むくみ|食欲|体調|"
    # 物・設備・人のトラブル（無くした・壊れた・はぐれた）
    r"失くし|なくし|落とし|盗まれ|壊れ|故障|漏れ|詰ま|あふれ|溢れ|迷子|はぐれ|行方|連絡が取れ|連絡がとれ|けんか|喧嘩|トラブル|"
    r"\b(complain|complaint|terrible|awful|too (cold|hot|loud|noisy|small|slow|long)|not enough|"
    r"unfair|dirty|smell|smells|can'?t sleep|cannot sleep|waiting|slow|worried|scared|afraid|sick|hurts|broken|"
    r"ran out|running out|no (water|food|blanket|blankets|power)|don'?t have|doesn'?t work|not working|"
    r"fever|cough|vomit|nausea|diarrhea|dizzy|headache|stomach ?ache|bleed|bleeding|injur|wound|fracture|collapse|"
    r"unconscious|chills|dehydrat|rash|itch|allergic|seizure|numb|lost|missing|stolen|leak|clogged|fight|argument)",
    re.IGNORECASE)

_GREETING = re.compile(
    r"^\s*(こんにちは|こんばんは|おはよう(ございます)?|ありがとう(ございます|ございました)?|よろしく(お願いします)?|了解|"
    r"hello|hi|hey|thanks|thank you|ok|okay)\s*[。！!.]*\s*$",
    re.IGNORECASE)
# 言葉の表に当たらなかったときの寄せ先（話題から決める）: 体調・安全・家族・乳幼児は「困りごと」、ほかの話題は「質問」
_FALLBACK_TROUBLE = ("medical", "safety", "family", "baby")
CLASSIFY_VERSION = 2  # 分類の言葉の表を変えたら上げる。起動時に古い版の発言を分類し直す（対応状況は保つ）
# 場所・時刻・数を聞く言葉（「薬はどこでもらえますか」を要望ではなく質問にする）
_WH = re.compile(
    r"どこ|いつ|何時|なんじ|なに|何|誰|だれ|いくら|いくつ|どれ|どの|どちら|"
    r"\b(where|when|what|who|which|why|how (many|much|long|do|can|to))\b",
    re.IGNORECASE)
_QUESTION = re.compile(
    r"[?？]|どう|ですか|ますか|ませんか|でしょうか|教えて|かな$|の$|は$|って$|"
    r"\b(is there|are there|do you|does|can i|could i|should i|is it)\b",
    re.IGNORECASE)


def _classify(text: str) -> tuple[str, str, str]:
    """(kind, topic, 根拠)。根拠は greeting／wh／request／trouble／question（言葉の表で決まった）か
    fallback（話題からの寄せ）／none（何も当たらない）。"""
    t = text.strip()
    low = t.lower()
    topic = "other"
    best = 0
    for key, (_label, _cat, words) in TOPICS.items():
        n = sum(1 for w in words if w.lower() in low)
        if n > best:
            best, topic = n, key
    if _GREETING.match(t):
        return "other", topic, "greeting"  # あいさつ・お礼だけの発言（「こんにちは」の「は」を質問と取らない）
    if _WH.search(t):
        return "question", topic, "wh"
    if _REQUEST.search(t):
        return "request", topic, "request"
    if _TROUBLE.search(t):
        return "trouble", topic, "trouble"
    if _QUESTION.search(t):
        return "question", topic, "question"
    if topic in _FALLBACK_TROUBLE:
        return "trouble", topic, "fallback"  # 「熱」など話題の言葉だけの発言も、体調・安全・家族・乳幼児なら困りごと
    if topic != "other":
        return "question", topic, "fallback"  # 「水の配給」のような名詞だけの発言は、その話題を知りたい質問
    return "other", topic, "none"


def classify(text: str, lang: str = "ja") -> tuple[str, str]:
    """言葉の一致だけの (kind, topic)。緊急（ガード）は呼び出し側が決める（ai_evacuee.guard_kind）。"""
    kind, topic, _ = _classify(text)
    return kind, topic


# --- 埋め込み（bge-m3）で近い例文の分類を採る ----------------------------------
# 言葉の表で決まらない発言（「頭がぼーっとする」「赤ちゃんがずっと泣いてる」など）を、
# 例文のうち意味が一番近いものの種類・話題で分類する。生成モデルは使わない（CPU で遅いため）。
# kind は question／request／trouble だけ（緊急はガード、その他はあいさつ）。避難者の実際の言い方で書く
EXAMPLES: list[tuple[str, str, str]] = [
    # 配給・食事
    ("question", "food", "ご飯はいつ配られるの"), ("question", "food", "今日の夕食なに"),
    ("request", "food", "おにぎりもう一個もらえる？"), ("request", "food", "アレルギー対応の食事ある？"),
    ("trouble", "food", "お腹すいた"), ("trouble", "food", "配給が足りなくてもらえなかった"),
    ("question", "food", "What time is dinner?"), ("trouble", "food", "I'm hungry, I missed the meal"),
    # 水
    ("question", "water", "水どこでもらえる"), ("request", "water", "飲み水がほしい"),
    ("trouble", "water", "水がもうない"), ("trouble", "water", "のど乾いた"),
    ("question", "water", "Where can I get water?"), ("request", "water", "Can I have some drinking water"),
    # 薬・体調・医療（症状の言い方を多めに）
    ("trouble", "medical", "熱が出た"), ("trouble", "medical", "咳が止まらない"), ("trouble", "medical", "気持ち悪い 吐きそう"),
    ("trouble", "medical", "お腹こわした"), ("trouble", "medical", "頭がくらくらする"), ("trouble", "medical", "転んで足をけがした"),
    ("trouble", "medical", "血圧の薬が切れた"), ("trouble", "medical", "持病の薬を家に置いてきた"),
    ("trouble", "medical", "だるくて動けない"), ("trouble", "medical", "胸がどきどきして落ち着かない"),
    ("trouble", "medical", "気分が沈んで何もしたくない"), ("trouble", "medical", "お年寄りがぐったりしてる"),
    ("trouble", "medical", "子どもが鼻水と咳"), ("trouble", "medical", "My child has a runny nose and cough"),
    ("request", "medical", "お医者さんに診てほしい"), ("request", "medical", "痛み止めもらえる？"),
    ("question", "medical", "救護所はどこ"), ("question", "medical", "薬を処方してもらえるところある？"),
    ("trouble", "medical", "I have a high fever"), ("trouble", "medical", "I feel dizzy and weak"),
    ("trouble", "medical", "My insulin ran out"), ("request", "medical", "I need to see a doctor"),
    # トイレ
    ("question", "toilet", "トイレどこ"), ("trouble", "toilet", "トイレが汚くて使えない"),
    ("trouble", "toilet", "トイレの行列が長すぎる"), ("request", "toilet", "トイレットペーパー補充して"),
    ("question", "toilet", "Where is the restroom?"), ("trouble", "toilet", "The toilet is clogged"),
    # 風呂・洗濯・衛生
    ("question", "bath", "お風呂入れるところある？"), ("request", "bath", "シャワー浴びたい"),
    ("trouble", "bath", "何日も体を洗えてない"), ("request", "bath", "歯ブラシほしい"),
    ("question", "bath", "Can I do laundry somewhere?"), ("request", "bath", "I need soap and a towel"),
    # 寝具・寒さ暑さ
    ("request", "bedding", "毛布もう一枚ほしい"), ("trouble", "bedding", "床が固くて眠れない"),
    ("trouble", "bedding", "夜寒くて震える"), ("trouble", "bedding", "暑くて汗だく"),
    ("question", "bedding", "段ボールベッドある？"), ("trouble", "bedding", "It's freezing at night"),
    ("request", "bedding", "Can I get a blanket"),
    # 生活環境
    ("trouble", "env", "隣のいびきがうるさい"), ("trouble", "env", "場所が狭くて足も伸ばせない"),
    ("trouble", "env", "着替える場所がない"), ("request", "env", "間仕切りがほしい"),
    ("trouble", "env", "ゴミのにおいがきつい"), ("question", "env", "ゴミはどこに捨てる"),
    ("trouble", "env", "Too noisy to rest"), ("request", "env", "I need some privacy"),
    # 充電・通信
    ("question", "charge", "スマホ充電できる？"), ("trouble", "charge", "携帯の電池がもうない"),
    ("question", "charge", "wifiのパスワード教えて"), ("trouble", "charge", "電波が入らない"),
    ("question", "charge", "Where can I charge my phone?"), ("trouble", "charge", "My phone is dead"),
    # 家族・安否
    ("trouble", "family", "母と連絡がつかない"), ("trouble", "family", "おじいちゃんがどこにいるかわからない"),
    ("question", "family", "ほかの避難所にいる人を調べられる？"), ("request", "family", "家族を探してほしい"),
    ("trouble", "family", "I can't reach my parents"), ("question", "family", "How can I find my family?"),
    # 乳幼児・子ども
    ("request", "baby", "赤ちゃんのミルクほしい"), ("request", "baby", "おむつもらえる？"),
    ("question", "baby", "授乳できる部屋ある？"), ("trouble", "baby", "子どもがずっと泣きやまない"),
    ("trouble", "baby", "子どもの遊ぶ場所がない"), ("request", "baby", "I need diapers for my baby"),
    ("question", "baby", "Is there baby formula?"),
    # ペット
    ("question", "pet", "犬連れてきていい？"), ("question", "pet", "ペットはどこにいればいい"),
    ("trouble", "pet", "猫のえさがない"), ("trouble", "pet", "犬の鳴き声がうるさい"),
    ("question", "pet", "Can I bring my dog inside?"),
    # 防犯・不安
    ("trouble", "safety", "夜トイレに行くのが怖い"), ("trouble", "safety", "知らない人にじろじろ見られる"),
    ("trouble", "safety", "財布を盗られたかも"), ("request", "safety", "夜の見回りしてほしい"),
    ("trouble", "safety", "不安で落ち着かない"), ("trouble", "safety", "I feel unsafe at night"),
    # 運営・対応
    ("question", "staff", "受付どこ"), ("trouble", "staff", "職員の対応が冷たい"),
    ("question", "staff", "ボランティアしたい 誰に言えばいい"), ("trouble", "staff", "聞いても誰もわからない"),
    ("question", "staff", "Who is in charge here?"),
    # 手続き・制度
    ("question", "procedure", "罹災証明はどうやって取るの"), ("question", "procedure", "仮設住宅の申し込み"),
    ("question", "procedure", "保険の手続きどうすれば"), ("request", "procedure", "書類の書き方を手伝ってほしい"),
    ("question", "procedure", "How do I apply for temporary housing?"),
    # 災害・交通・情報
    ("question", "info", "いつ家に帰れる"), ("question", "info", "電車動いてる？"),
    ("question", "info", "また大きい地震来る？"), ("trouble", "info", "家が心配で眠れない"),
    ("question", "info", "停電いつ直る"), ("question", "info", "Is the train running?"),
    ("question", "info", "When can we go home?"),
    # 施設・設備・時間
    ("question", "facility", "消灯何時"), ("question", "facility", "車どこに停めればいい"),
    ("question", "facility", "夜は外に出られる？"), ("trouble", "facility", "階段しかなくて上がれない"),
    ("question", "facility", "喫煙所ある？"), ("question", "facility", "What time are lights out?"),
]
# これ未満なら埋め込みの結果を使わない。bge-m3 は短い文どうしだと関係なくても 0.6 前後になる
# （「こんにちは」→ 0.61）ので evidence の 0.55 より高め。症状の言い換え（「喉がイガイガする」0.67）は通る
AI_MIN_SIM = 0.65
_ex_matrix = None  # 例文の埋め込み（最初の呼び出しで一括計算）
_bg: set = set()   # create_task の参照（途中で消されないように）


async def _examples_matrix():
    global _ex_matrix
    if _ex_matrix is None:
        from .evidence import _norm
        vecs = []
        for i in range(0, len(EXAMPLES), 16):
            vecs += await llm.embed([e[2] for e in EXAMPLES[i:i + 16]])
        _ex_matrix = _norm(vecs)
    return _ex_matrix


async def classify_ai(text: str, lang: str = "ja") -> tuple[str, str, float]:
    """埋め込みで一番近い例文の (kind, topic, 類似度)。kind は一番近い 1 文、topic は上位 3 文を類似度で重み付けした多数決
    （kind を多数決にすると「赤ちゃんが泣いてる」がミルク・おむつの要望に引っ張られた）。"""
    from .evidence import _norm
    mat = await _examples_matrix()
    q = _norm((await llm.embed([text.strip()]))[0])
    sims = mat @ q
    top = sims.argsort()[::-1][:3]
    tv: Counter = Counter()
    for i in top:
        tv[EXAMPLES[i][1]] += float(sims[i])
    return EXAMPLES[top[0]][0], tv.most_common(1)[0][0], float(sims[top[0]])


async def classify_full(text: str, lang: str = "ja") -> tuple[str, str, str]:
    """(kind, topic, how)。言葉の表で決まればそれ、決まらなければ埋め込み。how は rule／embed／rule+embed。
    - 疑問詞・要望・困りごと・「？」などに当たった → kind はそのまま。話題が other のときだけ埋め込みの話題
    - 話題からの寄せ・何も当たらない → 埋め込み（類似度が AI_MIN_SIM 未満なら言葉の一致のまま）
    - あいさつ → other のまま。埋め込みが失敗したら言葉の一致の結果を返す"""
    kind, topic, basis = _classify(text)
    if basis == "greeting" or not text.strip():
        return kind, topic, "rule"
    strong = basis in ("wh", "request", "trouble", "question")
    if strong and topic != "other":
        return kind, topic, "rule"
    try:
        ak, at, sim = await classify_ai(text, lang)
    except Exception as e:  # noqa: BLE001  Ollama が止まっていても記録は言葉の一致で続ける
        log.warning("classify_ai failed: %s", e)
        return kind, topic, "rule"
    if sim < AI_MIN_SIM:
        return kind, topic, "rule"
    if strong:
        return kind, at, "rule+embed"
    return ak, at, "embed"


async def refine(log_id: int, text: str, lang: str = "ja") -> None:
    """記録済みの発言を classify_full で分類し直す（チャットの答えを遅らせないよう後から）。緊急は触らない。"""
    kind, topic, how = await classify_full(text, lang)
    db.execute("UPDATE chat_logs SET kind=?, topic=?, classify_how=? WHERE id=? AND role='user' "
               "AND COALESCE(kind,'') != 'emergency'", (kind, topic, how, log_id))


def refine_later(log_id: int, text: str, lang: str = "ja") -> None:
    """refine をバックグラウンドで走らせる（/api/chat から）。"""
    tk = asyncio.create_task(refine(log_id, text, lang))
    _bg.add(tk)
    tk.add_done_callback(_bg.discard)


async def backfill_ai() -> int:
    """埋め込みでまだ見ていない発言（classify_how が NULL）を classify_full で分類し直す（起動時・warmup の後）。
    対応状況・回答の結果は変えない。緊急（ガード）は対象外。"""
    rows = db.query("SELECT id, lang, content FROM chat_logs WHERE role='user' AND classify_how IS NULL "
                    "AND COALESCE(kind,'') != 'emergency' AND COALESCE(outcome,'') != 'guard'")
    if not rows:
        return 0
    try:
        await _examples_matrix()  # Ollama が止まっていたら何もしない（NULL のまま次の起動で見直す）
    except Exception as e:  # noqa: BLE001
        log.warning("backfill_ai skipped: %s", e)
        return 0
    for r in rows:
        await refine(r["id"], r["content"], r["lang"] or "ja")
    return len(rows)


# --- 記録 ------------------------------------------------------------------
def record(session: str, lang: str, role: str, content: str, guard: str | None = None,
           evacuee_id: int | None = None, kind: str | None = None, topic: str | None = None,
           how: str | None = None) -> int:
    """チャットの1発言を残す。避難者の発言（role='user'）は種類・話題を付けて未対応にする。
    kind／topic を渡されたらそれを使い、無ければ言葉の一致（classify）で付ける。
    埋め込みでの見直しは後から refine_later（how が NULL の行は未確認の印）。
    自己登録した人のスマホからなら受付番号（evacuee_id）を付け、運営画面で本人とひも付く。"""
    status = None
    if role == "user":
        if not (kind and topic):
            kind, topic = classify(content, lang)
            how = None
        if guard:
            kind = "emergency"
            topic = "safety" if guard == "violence" else "medical"
            how = "rule"
        status = "open"
    else:
        kind = topic = how = None
    return db.execute(
        "INSERT INTO chat_logs(session, lang, role, content, created_at, kind, topic, status, evacuee_id, classify_how) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (session[:64], lang, role, content[:2000], now().isoformat(), kind, topic, status, evacuee_id, how))


def set_outcome(log_id: int, outcome: str) -> None:
    if log_id and outcome in OUTCOMES:
        db.execute("UPDATE chat_logs SET outcome=? WHERE id=?", (outcome, log_id))


def set_status(log_id: int, status: str) -> bool:
    if status not in ("open", "done"):
        return False
    with db.conn() as c:
        cur = c.execute("UPDATE chat_logs SET status=?, handled_at=? WHERE id=? AND role='user'",
                        (status, now().isoformat() if status == "done" else None, log_id))
        return cur.rowcount > 0


def backfill() -> int:
    """列を足す前からあった発言に種類・話題を付ける（起動時に1回）。
    分類の版（CLASSIFY_VERSION）が上がっていれば、全部の発言を分類し直す（対応状況・回答の結果は保つ）。"""
    stored = db.get_setting("classify_version")
    reclassify_all = str(stored or "") != str(CLASSIFY_VERSION)
    where = "" if reclassify_all else " AND kind IS NULL"
    rows = db.query(f"SELECT id, lang, content, outcome FROM chat_logs WHERE role='user'{where}")
    if reclassify_all:
        db.set_setting("classify_version", CLASSIFY_VERSION)
    with db.conn() as c:
        for r in rows:
            kind, topic = classify(r["content"], r["lang"] or "ja")
            if r["outcome"] == "guard":  # ガードに当たった発言は緊急のまま
                kind, topic = "emergency", ("safety" if topic == "other" else topic)
            # classify_how は NULL に戻す → warmup の後に backfill_ai が埋め込みで見直す
            c.execute("UPDATE chat_logs SET kind=?, topic=?, status=COALESCE(status,'open'), classify_how=NULL "
                      "WHERE id=?", (kind, topic, r["id"]))
    return len(rows)


# --- 集計 ------------------------------------------------------------------
def _since_clause(hours: int | None) -> tuple[str, list]:
    if not hours:
        return "", []
    return " AND created_at >= ?", [(now() - timedelta(hours=hours)).isoformat()]


def _normalize(text: str) -> str:
    """似た質問をまとめる用（空白・記号を落として小文字に）。"""
    return re.sub(r"[\s、。，．,.!！?？「」『』()（）・…~〜ー-]+", "", text).lower()


def list_voices(kind: str = "", topic: str = "", status: str = "", lang: str = "", q: str = "",
                hours: int | None = None, limit: int = 300) -> list[dict]:
    where, params = ["role='user'"], []
    if kind in KINDS:
        where.append("kind=?"); params.append(kind)
    if topic in TOPIC_LABEL:
        where.append("topic=?"); params.append(topic)
    if status in ("open", "done"):
        where.append("status=?"); params.append(status)
    if lang in ("ja", "en"):
        where.append("lang=?"); params.append(lang)
    if q:
        where.append("content LIKE ?"); params.append(f"%{q}%")
    sc, sp = _since_clause(hours)
    rows = db.query(
        "SELECT c.*, e.name AS evacuee_name, e.hh AS evacuee_hh FROM chat_logs c"
        " LEFT JOIN evacuees e ON e.id = c.evacuee_id"
        f" WHERE {' AND '.join('c.' + w for w in where)}{sc.replace('created_at', 'c.created_at')}"
        " ORDER BY c.id DESC LIMIT ?",
        params + sp + [limit])
    return [_view(r) for r in rows]


def _view(r: dict) -> dict:
    r = dict(r)
    r["kind_label"] = KINDS.get(r.get("kind") or "other", "その他")
    r["topic_label"] = TOPIC_LABEL.get(r.get("topic") or "other", "その他")
    r["outcome_label"] = OUTCOMES.get(r.get("outcome") or "", "")
    r["hhmm"] = (r["created_at"] or "")[11:16]
    r["date"] = (r["created_at"] or "")[5:10].replace("-", "/")
    return r


def summary(hours: int | None = None) -> dict:
    """件数のまとめ。ダッシュボードのカードと /staff/voices の上段に使う。"""
    sc, sp = _since_clause(hours)
    rows = db.query(f"SELECT kind, topic, status, outcome, lang, content, created_at FROM chat_logs "
                    f"WHERE role='user'{sc}", sp)
    kinds = Counter(r["kind"] or "other" for r in rows)
    # 「未対応」は要望・困りごと・緊急だけ数える（質問は答えが返っていれば運営の対応は要らない）
    open_ = Counter(r["kind"] for r in rows if r["status"] == "open" and r["kind"] in ACTIONABLE)
    topics: dict[str, dict] = defaultdict(lambda: {"total": 0, "open": 0, "none": 0, "latest": ""} | {k: 0 for k in KINDS})
    for r in rows:
        t = topics[r["topic"] or "other"]
        t["total"] += 1
        t[r["kind"] or "other"] += 1
        if r["status"] == "open" and (r["kind"] or "other") in ACTIONABLE:
            t["open"] += 1
        if r["outcome"] == "none":
            t["none"] += 1
        if r["created_at"] > t["latest"]:
            t["latest"] = r["created_at"]
    topic_rows = [{"key": k, "label": TOPIC_LABEL.get(k, k), "notice_category": TOPICS.get(k, ("", "other", []))[1],
                   **v, "latest_hhmm": v["latest"][5:16].replace("T", " ")}
                  for k, v in topics.items()]
    topic_rows.sort(key=lambda x: (-x["total"], x["key"]))
    return {
        "total": len(rows),
        "kinds": {k: kinds.get(k, 0) for k in KINDS},
        "open": {k: open_.get(k, 0) for k in KINDS},
        "open_total": sum(open_.values()),
        "unanswered": sum(1 for r in rows if r["outcome"] == "none"),
        "langs": dict(Counter(r["lang"] or "ja" for r in rows)),
        "topics": topic_rows,
        "since": min((r["created_at"] for r in rows), default=None),
    }


def frequent(hours: int | None = None, limit: int = 12, kinds: tuple[str, ...] = ("question",)) -> list[dict]:
    """同じ内容の発言をまとめて多い順に。答えられなかった回数も付ける（案内・お知らせに載せる候補を見る）。"""
    sc, sp = _since_clause(hours)
    marks = ",".join("?" * len(kinds))
    rows = db.query(f"SELECT content, lang, topic, outcome, created_at FROM chat_logs "
                    f"WHERE role='user' AND kind IN ({marks}){sc} ORDER BY id DESC", list(kinds) + sp)
    groups: dict[str, dict] = {}
    for r in rows:
        key = _normalize(r["content"])
        g = groups.setdefault(key, {"text": r["content"], "lang": r["lang"], "topic": r["topic"] or "other",
                                    "count": 0, "none": 0, "latest": r["created_at"]})
        g["count"] += 1
        if r["outcome"] == "none":
            g["none"] += 1
    out = sorted(groups.values(), key=lambda g: (-g["count"], -g["none"], g["latest"]))
    for g in out:
        g["topic_label"] = TOPIC_LABEL.get(g["topic"], g["topic"])
        g["latest_hhmm"] = g["latest"][5:16].replace("T", " ")
    return out[:limit]


def digest_lines(hours: int | None = 24, limit: int = 60) -> list[str]:
    """AI（運営者の助手）に渡す匿名の発言一覧。種類・話題・回答の結果を付けた1行ずつ。"""
    rows = list_voices(hours=hours, limit=limit)
    lines = []
    for r in reversed(rows):  # 古い順
        flag = "未対応" if r["status"] == "open" else "対応済"
        oc = f"／{r['outcome_label']}" if r["outcome_label"] else ""
        lines.append(f"- {r['hhmm']} [{r['kind_label']}／{r['topic_label']}／{flag}{oc}] {r['content'][:120]}")
    return lines
