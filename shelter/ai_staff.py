"""運営者向けの助手。判断は人がする前提で、AI は根拠付きの下書き・要約だけを出す。

タスク:
- manual   マニュアルに聞く（RAG・出典付き）
- care     要配慮者の洗い出し（SQL で抽出した一覧 → 声かけの優先順の要約）
- supplies 不足物資の見積もり（ルール計算の表 → 補足コメント）
- poster   掲示文を作る（決定事項 → 掲示文・放送原稿・やさしい日本語・英語）
- handover 引継ぎ要約（お知らせ・在所状況・匿名の相談傾向 → 次の担当への引継ぎ）
- voices   避難者の声の整理（チャットの要望・不満・質問の一覧 → 多い相談・急ぐもの・お知らせ候補）
"""
from __future__ import annotations

from . import notices, rag, stats, voices
from .config import now
from .schema.standard_form import ALLERGY, CARE, REL

BASE = ("あなたは日本の避難所の運営を手伝う助手です。運営者（市職員・自主防災組織・ボランティア）に日本語で答えます。"
        "判断は人がする前提で、根拠を示して簡潔に書く。医療の判断はしない。与えられた情報に無いことは「不明」と書く。")


class TaskError(ValueError):
    pass


async def build(task: str, body: dict) -> tuple[list[dict], list[dict]]:
    """(messages, sources)。sources は画面の「根拠」欄に出す。"""
    if task == "manual":
        return await _manual(body)
    if task == "care":
        return _care(body)
    if task == "supplies":
        return _supplies(body)
    if task == "poster":
        return _poster(body)
    if task == "handover":
        return _handover(body)
    if task == "voices":
        return _voices(body)
    raise TaskError(f"不明なタスク: {task}")


async def _manual(body: dict):
    q = (body.get("question") or "").strip()
    if not q:
        raise TaskError("質問を入力してください")
    hits = await rag.search(q)
    if not hits:
        raise TaskError("マニュアルがまだ取り込まれていません。設定 → 「マニュアルを取り込む」を実行してください")
    ctx = "\n\n".join(f"[{i + 1}] 出典: {h['title']} › {h['section']}\n{h['text']}"
                      for i, h in enumerate(hits))
    sys = (BASE + "\n下の【マニュアル抜粋】だけを根拠に答える。文中で根拠に [1] のように番号を付ける。"
           "抜粋に答えが無ければ「マニュアルの抜粋には見当たりません」と書き、確認すべき相手を挙げる。"
           "最後に「出典:」として使った番号と文書名・ページを列挙する。")
    msgs = [{"role": "system", "content": sys},
            {"role": "user", "content": f"【マニュアル抜粋】\n{ctx}\n\n【質問】{q}"}]
    sources = [{"type": "manual", "n": i + 1, "title": h["title"], "section": h["section"],
                "score": h["score"], "text": h["text"][:200]} for i, h in enumerate(hits)]
    return msgs, sources


def _needs_care(p: dict) -> bool:
    return bool(p["care"]) or (p["age"] or 0) >= 75


def care_list() -> list[dict]:
    """代表者か登録した同行者の誰かに要配慮（または 75 歳以上）がある受付。"""
    return [r for r in stats.active_evacuees() if any(_needs_care(p) for p in stats.people_of(r))]


def _care(body: dict):
    rows = care_list()
    if not rows:
        raise TaskError("在所者に要配慮の登録がありません")
    # AI には短い区分名を渡す（「透析・酸素等」のような例示を事実と取り違えさせない）
    short = {**CARE, "MED": "医療機器", "INF": "乳幼児同伴"}
    lines = []
    for r in rows:
        care = "・".join(short[c] for c in r["care"]) or "なし"
        alg = "・".join(ALLERGY[a] for a in r["alg"])
        age = f"{r['age']}歳" if r["age"] is not None else "年齢不明"
        lines.append(f"- 受付{r['id']} {r['name']}（{age}・同行{r['hh'] - 1}人）区分: {care}"
                     + (f" アレルギー: {alg}" if alg else "") + (f" メモ: {r['note']}" if r["note"] else ""))
        for m in r["members"]:
            if not _needs_care(m):
                continue
            m_age = f"{m['age']}歳" if m["age"] is not None else "年齢不明"
            m_care = "・".join(short[c] for c in m["care"]) or "なし"
            m_alg = "・".join(ALLERGY[a] for a in m["alg"])
            lines.append(f"  - 同行 {m['name']}（{REL.get(m.get('rel') or '', '同行者')}・{m_age}）区分: {m_care}"
                         + (f" アレルギー: {m_alg}" if m_alg else ""))
    sys = (BASE + "\n要配慮者の一覧から、運営者が今日「優先して声をかける順」を上位から最大8人まで、"
           "1人1行で理由（区分・年齢・メモ）とともに挙げる。続けて、避難所全体として手配を検討すべきこと"
           "（福祉避難所への移送の相談、医療機器の電源、アレルギー対応食、女性・乳幼児スペース等）を3点まで。"
           "理由には一覧の区分・年齢・メモに書いてあることだけを使い、病名や状態を推測で足さない。"
           "命や医療の継続に関わる人（医療機器・薬の残りが少ない・要介護の高齢者・妊産婦・乳児）を先にする。")
    msgs = [{"role": "system", "content": sys},
            {"role": "user", "content": f"現在 {now():%m/%d %H:%M} の要配慮者一覧:\n" + "\n".join(lines)}]
    return msgs, [{"type": "roster", "count": len(rows)}]


def _supplies(body: dict):
    s = stats.summary()
    table = stats.supplies(s)
    lines = [f"- {x['item']}: 必要 {x['need']} {x['unit']}（{x['basis']}）"
             + (f" / 備蓄 {x['stock']} / 不足 {x['short']}" if x["stock"] is not None else " / 備蓄 未入力")
             for x in table]
    facts = (f"在所 {s['total']}人（{s['households']}世帯）、女性 {s['female']}、男性 {s['male']}、不明 {s['unknown']}、"
             f"乳幼児 {s['infants']}、65歳以上 {s['elderly']}、要配慮 "
             + "・".join(f"{CARE[k]}{v}" for k, v in s["care"].items() if v)
             + f"、ペット {s['pets']}")
    sys = (BASE + "\n下の計算表（ルールで計算済み。数値は変えない）を見て、運営者への補足を箇条書き5点までで書く。"
           "例: 不足が大きい順の手配、本部への要請の文例、季節・要配慮者に応じて追加で要りそうな物（数は書かない）。")
    msgs = [{"role": "system", "content": sys},
            {"role": "user", "content": f"状況: {facts}\n計算表（1日分）:\n" + "\n".join(lines)}]
    return msgs, [{"type": "rule", "items": len(table)}]


def _poster(body: dict):
    text = (body.get("text") or "").strip()
    if not text:
        raise TaskError("掲示にしたい決定事項を入力してください")
    sys = (BASE + "\n運営者の決定事項を、次の4つに書き直す。内容を足さない・時刻や場所を変えない。\n"
           "## 掲示文（見出し＋要点3行以内。大きく貼る紙用）\n## 館内放送の原稿（2回繰り返す前提、30秒以内）\n"
           "## やさしい日本語（漢字にふりがなは不要。短い文、1文1つのこと）\n## English（plain, short）")
    return ([{"role": "system", "content": sys}, {"role": "user", "content": text}], [])


def _handover(body: dict):
    s = stats.summary()
    recent = notices.list_notices("ja", limit=10)
    notice_lines = "\n".join(f"- {n['posted_at'][5:16].replace('T', ' ')}（{n['category_label']}）{n['title']}: {n['body']}"
                             for n in recent) or "- なし"
    # 匿名の相談傾向（避難者の声の話題別件数。本文は渡さない）
    v = voices.summary()
    since = v["since"]
    topic_line = "・".join(f"{t['label']}{t['total']}件（未対応{t['open']}）" for t in v["topics"]
                          if t["key"] != "other") or "記録なし"
    topics = {t["label"]: t["total"] for t in v["topics"]}
    extra = (body.get("text") or "").strip()
    sys = (BASE + "\n次の担当者への引継ぎメモを書く。構成: ## いまの状況（数字）／## これまでの経緯（お知らせから時系列で）"
           "／## 避難者から多い相談（件数から）／## 次の担当がやること（3点まで）。与えた情報だけを使う。")
    user = (f"現在 {now():%m/%d %H:%M}\n在所 {s['total']}人・{s['households']}世帯・要配慮の受付 {s['care_people']}件・"
            f"乳幼児 {s['infants']}・ペット {s['pets']}・退所済み {s['checked_out']}件\n"
            f"お知らせ（新しい順）:\n{notice_lines}\n避難者チャットの相談の傾向（匿名・{v['total']}件"
            f"{'・' + since[5:16].replace('T', ' ') + '以降' if since else ''}）: {topic_line}"
            + (f"\n運営者メモ: {extra}" if extra else ""))
    return ([{"role": "system", "content": sys}, {"role": "user", "content": user}],
            [{"type": "topics", "topics": topics}])


def _voices(body: dict):
    try:
        hours = int(body.get("hours") or 24)
    except (TypeError, ValueError):
        hours = 24
    h = hours if hours > 0 else None
    lines = voices.digest_lines(h)
    if not lines:
        raise TaskError("避難者チャットの発言がまだありません")
    s = voices.summary(h)
    sys = (BASE + "\n避難者チャットの発言一覧（匿名・時刻順・[種類／話題／対応状況／回答の結果]付き）を読み、運営者向けに整理する。"
           "構成: ## 多い相談・要望（件数の多い順に3〜5点。似た発言はまとめ、数えた件数を数字で書く。例: 「配給の時刻 3件」）"
           "／## 急いで対応したい不満・困りごと（安全・体調・薬・乳幼児・高齢者に関わるものを先に。無ければ「なし」）"
           "／## お知らせや掲示に出すと減りそうな質問（「答えられず」の多いもの。見出し案を1行ずつ）"
           "／## 運営が今日やること（3点まで）。一覧に無いことは書かない。個人を特定する書き方をしない。")
    k = s["kinds"]
    period = f"直近{hours}時間" if h else "全期間"
    user = (f"現在 {now():%m/%d %H:%M}・{period}・{len(lines)}件を表示（質問{k['question']}・要望{k['request']}・"
            f"困りごと{k['trouble']}・緊急{k['emergency']}・未対応{s['open_total']}・答えられず{s['unanswered']}）\n"
            + "\n".join(lines))
    return ([{"role": "system", "content": sys}, {"role": "user", "content": user}],
            [{"type": "voices", "count": len(lines)}])
