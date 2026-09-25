"""避難者向けチャット: お知らせ・避難所の案内・FAQ だけを根拠に、選んだ言語で短く答える。

- 暴力・性被害の相談と、命に関わる急病は AI に答えさせず、決まった案内を返す（安全のためのガード）
- FAQ とほぼ同じ質問は FAQ の答えをそのまま返し、それ以外は近い根拠の数行だけで AI に答えさせる（evidence.py）
- AI は「考えてから答える」2段階（llm.Plan）: まず「使う根拠の番号と要点」を短く書かせ、
  根拠が無ければ生成せずに「その情報はまだありません」を返す。あれば考えを会話に足して答えだけを書かせる
- 指示の前置きは毎回同じにして、Ollama の前置きキャッシュを効かせる
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from . import db, evidence, llm, voices
from .config import DATA_DIR, now

MAX_HISTORY = 6  # 往復3回分


@lru_cache(maxsize=8)
def _read(path: Path, mtime: float) -> str:  # mtime はキャッシュ無効化用
    return path.read_text(encoding="utf-8")


def load_doc(stem: str, lang: str) -> str:
    for lg in (lang, "ja"):
        p = DATA_DIR / f"{stem}.{lg}.md"
        if p.exists():
            return _read(p, p.stat().st_mtime)
    return ""


# --- ガード（AI を通さず決まった案内を返す） ------------------------------
_VIOLENCE = re.compile(
    r"性被害|性暴力|痴漢|レイプ|強姦|わいせつ|盗撮|のぞき|つきまと|ストーカ|DV|ＤＶ|暴力|殴ら|叩かれ|"
    r"襲われ|触られ|脅さ|虐待|\b(rape|raped|assault|assaulted|abuse|abused|harass|harassed|"
    r"harassment|stalk|stalker|stalking|violence|violent|groped|molest)",
    re.IGNORECASE)
_EMERGENCY = re.compile(
    r"意識がない|反応がない|息をしていない|呼吸していない|呼吸がない|心臓が止ま|倒れて動かない|けいれん|痙攣|"
    r"大出血|血が止まらない|胸が(痛|苦し)|息ができない|息が苦し|ろれつ|"
    r"\b(unconscious|not breathing|no pulse|cardiac|seizure|heavy bleeding|chest pain|"
    r"can't breathe|cannot breathe|stroke)\b",
    re.IGNORECASE)

GUARD_TEXT = {
    ("violence", "ja"): (
        "話してくれてありがとうございます。あなたは悪くありません。\n"
        "- まず安全な場所へ。人の多い場所や運営本部（受付）の近くにいてください\n"
        "- 運営本部の女性スタッフに「相談したい」とだけ伝えれば、別の場所で話を聞きます\n"
        "- 危険が迫っているときは警察 110。電話がつながれば 性暴力ワンストップ支援センター #8891、DV相談ナビ #8008\n"
        "このチャットは記録に名前を残しません。"),
    ("violence", "en"): (
        "Thank you for telling us. It is not your fault.\n"
        "- First, go to a safe place: near other people or the shelter office (reception)\n"
        "- Just tell a female staff member at the office \"I want to talk\". They will listen in a private place\n"
        "- If you are in danger, call the police at 110\n"
        "This chat does not record your name."),
    ("emergency", "ja"): (
        "緊急の可能性があります。今すぐ次のことをしてください。\n"
        "- 大声で周りの人と運営本部（受付）を呼ぶ。119 に通報する（つながらなければ運営本部へ）\n"
        "- 反応や普段どおりの呼吸がなければ、胸の真ん中を強く・速く押し続け、AED を持ってきてもらう（体育館入口・職員室前）\n"
        "- 出血は清潔な布で強く押さえ続ける\n"
        "AI は医療の判断ができません。人を呼ぶことを優先してください。"),
    ("emergency", "en"): (
        "This may be an emergency. Do this now:\n"
        "- Shout for help to people around you and the shelter office (reception). Call 119 (if it fails, tell the office)\n"
        "- If the person does not respond or is not breathing normally, push hard and fast on the center of the chest and ask someone to bring the AED (gym entrance / in front of the staff room)\n"
        "- For bleeding, press hard on the wound with a clean cloth and keep pressing\n"
        "The AI cannot make medical decisions. Getting people to help comes first."),
}


def guard_kind(message: str) -> str | None:
    """ガードに当たる種類（"violence" / "emergency"）。当たらなければ None。"""
    if _VIOLENCE.search(message):
        return "violence"
    if _EMERGENCY.search(message):
        return "emergency"
    return None


def guard_text(kind: str, lang: str) -> str:
    return GUARD_TEXT[(kind, "en" if lang == "en" else "ja")]


def guard(message: str, lang: str) -> str | None:
    k = guard_kind(message)
    return guard_text(k, lang) if k else None


# --- プロンプト ----------------------------------------------------------
# 答えは evidence.retrieve() が選んだ根拠の数行だけから作らせる（小さいモデルでも外しにくい）
NO_ANSWER = {
    "ja": "その情報はまだありません。受付（運営本部）で確認してください。",
    "en": "That information is not available yet. Please ask the shelter office (reception).",
}


def _system(lang: str, shelter_name: str) -> str:
    """毎回同じ前置き（Ollama の前置きキャッシュが効くよう、根拠や時刻は入れない）。"""
    if lang == "en":
        return f"""You are the information desk assistant of "{shelter_name}" (an evacuation shelter in Japan). You answer evacuees on their phones.
Rules:
- Answer ONLY with what is written in the EVIDENCE lines. Reuse their words, places, times and numbers as they are.
- If the EVIDENCE does not answer the question, reply exactly: "{NO_ANSWER['en']}"
- Lines starting with "Notice posted" are the latest information. If one answers the question, answer from it first (time, place, what, how many).
- Never invent notices, times, places or amounts. Mention a notice only if an EVIDENCE line starts with "Notice posted".
- Do not make medical judgments. For health problems, guide them to the first-aid space; if urgent, tell them to call for help loudly.
- Do not ask for names, addresses or other personal information.
- 1 to 3 short, simple sentences in English.
How to answer (two steps):
1. First write ONLY these two lines of notes (no answer yet):
   Evidence used: the numbers of the EVIDENCE lines that clearly answer the question (e.g. [2]). If a line starting with "Notice posted" answers it, you MUST choose that line (it beats an FAQ line that only says "we will post a notice"). If no line clearly answers the question, write "none".
   Key point: one line of what to answer from those lines (keep places, times and numbers as they are).
2. When asked "Write the answer.", write only the answer to the evacuee in 1 to 3 short sentences, following your notes. No evidence numbers, no labels, and do not add anything that is not in the evidence."""
    return f"""あなたは「{shelter_name}」（日本の避難所）の案内係です。避難者のスマホからの質問に答えます。
ルール:
- 「根拠」の行に書いてあることだけで答える。根拠の言葉・場所・時刻・数字はそのまま使う
- 根拠に答えが無いときは、推測せず「{NO_ANSWER['ja']}」とだけ答える
- 「お知らせ」で始まる根拠は一番新しい情報。質問の答えが書いてあれば、それを優先して時刻・場所・内容・数を答える
- 根拠に無いお知らせや時刻を作らない。お知らせに触れてよいのは「お知らせ」で始まる根拠があるときだけ
- 医療の判断はしない。体調の相談は救護スペースへ案内し、急ぐときは大声で人を呼ぶよう伝える
- 氏名・住所などの個人情報を聞かない
- 1〜3文で、短くやさしい日本語で答える
答え方（2段階）:
1. まず「考え」を次の2行だけ書く（答えはまだ書かない）
   使う根拠: 質問にはっきり答えている根拠の番号（例: [2]）。「お知らせ」で始まる根拠に答えがあれば必ずそれを選ぶ（「お知らせに出します」とだけ書いた FAQ より優先）。どの根拠も質問にはっきり答えていなければ「なし」
   要点: その根拠から答える内容を1行で（場所・時刻・数はそのまま）
2. 「答えを書いてください」と言われたら、その考えのとおりに避難者への答えだけを1〜3文で書く。根拠の番号や「要点:」は書かず、根拠に無いことを足さない"""


def search_query(message: str, history: list[dict]) -> str:
    """「それはどこ？」のような短い続きの質問は、直前の質問とつないで根拠を探す。"""
    if len(message) < 8:
        prev = next((h.get("content") for h in reversed(history)
                     if h.get("role") == "user" and isinstance(h.get("content"), str)), "")
        if prev:
            return f"{prev[:200]} {message}"
    return message


# --- 考えてから答える（2段階） -------------------------------------------------
# 1段階目: 根拠のどの行に答えがあるかと要点を短く書かせる（答えはまだ書かせない・画面には出さない）
# 2段階目: その考えを会話に足して、答えだけを書かせる（前置き〜根拠が同じなので Ollama が読み直さない）
# 2段階の答え方の説明は前置き（_system）に入れてある。質問には短い合図だけを付ける
THINK_PROMPT = {
    "ja": "まず「使う根拠:」「要点:」の2行だけ。",
    "en": "First, only the two lines \"Evidence used:\" and \"Key point:\".",
}
WRITE_PROMPT = {
    "ja": "答えを書いてください。",
    "en": "Write the answer.",
}

_PLAN_LINE = re.compile(r"(?:使う根拠|根拠|Evidence used|Evidence)\s*[:：]\s*(.*)", re.IGNORECASE)
_NONE = re.compile(r"なし|無し|ありません|\bnone\b", re.IGNORECASE)


def facts_from_plan(plan: str, facts: list[dict]) -> list[dict] | None:
    """考えの「使う根拠:」の行から、答えに使う根拠を選ぶ。
    「なし」なら None（＝生成せずに「その情報はまだありません」）。行が読めないときは全部を返す。"""
    m = _PLAN_LINE.search(plan)
    # 「使う根拠:」の行があればその行、無ければ考え全体（「[2]」とだけ書くことがある）から番号を拾う
    line = m.group(1) if m else plan
    nums = re.findall(r"\[(\d+)\]", line) or (re.findall(r"\d+", line) if m else [])
    idx = {int(i) - 1 for i in nums if 1 <= int(i) <= len(facts)}
    if idx:
        return [f for i, f in enumerate(facts) if i in idx]
    if _NONE.search(line.splitlines()[0] if line else "") or not facts:
        return None
    return facts


def think(plan: str, facts: list[dict], lang: str) -> tuple[str, list[dict] | None]:
    """考えを PC 側で確かめて (答えに使う考え, 使う根拠 or None) を返す。

    小さいモデルは、質問と同じ文の FAQ（「お知らせに出します」）を選んで、根拠にあるお知らせを見落とすことがある。
    お知らせは別枠の厳しめのしきい値で選んだ一番新しい情報なので、モデルが何かを選んだのにお知らせを外していたら
    お知らせを足し、考えを「使う根拠: [番号]」だけに書き直す（要点が「お知らせに出します」のままだと答えが引きずられる）。
    """
    chosen = facts_from_plan(plan, facts)
    if chosen is None:
        return plan, None
    missing = [f for f in facts if f["kind"] == "notice" and f not in chosen]
    if not missing:
        return plan, chosen
    chosen = [f for f in facts if f in chosen or f in missing]
    nums = ", ".join(f"[{i}]" for i, f in enumerate(facts, 1) if f in chosen)
    return f"{'Evidence used' if lang == 'en' else '使う根拠'}: {nums}", chosen


def answer_messages(msgs: list[dict], plan: str, lang: str) -> list[dict]:
    """1段階目の会話に考えを足し、答えだけを書かせる messages。"""
    return msgs + [{"role": "assistant", "content": plan.strip()},
                   {"role": "user", "content": WRITE_PROMPT["en" if lang == "en" else "ja"]}]


def build_messages(message: str, lang: str, history: list[dict],
                   facts: list[dict]) -> list[dict]:
    """Ollama に渡す messages（1段階目）。根拠と考えの指示は最後の質問と一緒に渡す（前置きを毎回同じにするため）。"""
    p = db.profile()
    lines = "\n".join(f"[{i}] {e['text']}" for i, e in enumerate(facts, 1))
    if lang == "en":
        user = f"EVIDENCE:\n{lines or '(none)'}\n\nQUESTION: {message[:1000]}\n\n{THINK_PROMPT['en']}"
    else:
        user = f"根拠:\n{lines or '（なし）'}\n\n質問: {message[:1000]}\n\n{THINK_PROMPT['ja']}"
    msgs = [{"role": "system", "content": _system(lang, p["name"])}]
    for h in history[-MAX_HISTORY:]:
        if h.get("role") in ("user", "assistant") and isinstance(h.get("content"), str):
            msgs.append({"role": h["role"], "content": h["content"][:1000]})
    msgs.append({"role": "user", "content": user})
    return msgs


_REF = re.compile(r"\s*\[\d+\]\s*")
# 「根拠: …\n\n答え: …」と根拠を書き写したとき、答えの部分だけを残す（考えの「要点:」を書き写したときも同じ）
_ANSWER_LABEL = re.compile(r"(?:答え|回答|答|要点|\bAnswer|\bANSWER|\bKey point|\bA)\s*[:：]\s*")
# 答えに「使う根拠: [2]」の行を書き写したときは、その行を消す
_PLAN_ECHO = re.compile(r"^\s*(?:使う根拠|Evidence used)\s*[:：].*$", re.IGNORECASE | re.MULTILINE)
# 根拠にお知らせが無いのに付けた「14:00 のお知らせでは、」「According to the 14:00 notice,」
_FAKE_NOTICE = re.compile(r"\d{1,2}[:：]\d{2}\s*の?お知らせ(では|によると)[、,]?\s*|"
                          r"According to the \d{1,2}:\d{2} notice,?\s*", re.IGNORECASE)


def clean_answer(answer: str, lang: str, facts: list[dict]) -> str:
    """小さいモデルの崩れを直す: 根拠の番号を消す／根拠に無いお知らせの引用を消す／
    「情報がありません」が混じったらその文だけにする。"""
    lg = "en" if lang == "en" else "ja"
    text = _PLAN_ECHO.sub("", answer)
    text = _ANSWER_LABEL.split(text)[-1]
    text = _REF.sub(" " if lg == "en" else "", text).strip()
    if not any(e["kind"] == "notice" for e in facts):
        text = _FAKE_NOTICE.sub("", text).strip()
        text = text[:1].upper() + text[1:]
    marker = "not available" if lg == "en" else "その情報はまだありません"
    if marker in text or not text:
        return NO_ANSWER[lg]
    return text


def evidence_sources(facts: list[dict], answer: str) -> list[dict]:
    """画面に出す根拠: 渡したお知らせ（質問に近いものだけ選んである）と、案内・FAQ を使ったこと。
    「情報がありません」と答えたときは出さない。"""
    if answer in NO_ANSWER.values():
        return []
    out = [{"type": "notice", "id": e["notice"]["id"], "posted_at": e["notice"]["posted_at"],
            "title": e["notice"]["title"], "hhmm": e["notice"]["hhmm"]}
           for e in facts if e["kind"] == "notice"]
    if any(e["kind"] in ("faq", "info") for e in facts):
        out.append({"type": "guide"})
    return out


async def prime_cache(langs: tuple[str, ...] = ("ja", "en")) -> None:
    """起動時に、根拠の埋め込みと言語ごとの前置きを先に作っておく（各言語の1問目を速くする）。"""
    await evidence.warm(langs)
    for lang in langs:
        await llm.complete(build_messages("こんにちは" if lang == "ja" else "Hello", lang, [], []),
                           max_tokens=1)


def log_chat(session: str, lang: str, role: str, content: str, guard: str | None = None,
             evacuee_id: int | None = None) -> int:
    """チャットを残す（避難者の発言は種類・話題付き → 運営画面「避難者の声」）。行 id を返す。
    自己登録した端末からの発言には受付番号を付ける。それ以外は匿名のまま。"""
    return voices.record(session, lang, role, content, guard, evacuee_id)


SUGGESTIONS = {
    "ja": ["次の配給はいつ？", "薬が無くなりそう", "トイレはどこ？", "スマホを充電したい", "体調が悪い"],
    "en": ["When is the next food distribution?", "I am running out of medicine",
           "Where is the toilet?", "I want to charge my phone", "I feel sick"],
}
