"""対応の優先順: 名簿の「伝えたいこと」「要配慮」と、避難者の声の未対応の相談を、人を単位に 1 本に並べる。

- AI は使わない（ルールで並べる。「なぜこの順か」は理由のレベルで答えられる）。新しい表も作らない
- 行の単位: 受付 1 件（世帯）＝ 1 行。代表者と同行者の理由をまとめる。匿名端末の相談は発言 1 件 1 行。退所済みは出さない
- レベル 3 段（3 至急・2 今日中・1 通常）。1 行のレベル＝その行の理由の最大値。
  伝えたいことのレベルは standard_form.NEEDS_URGENCY（5 段）を to_level() で 3 段に写したもの
- 「避難所に伝える」の未対応の案件（cases.py）も理由として束ねる
- 並び: レベル降順 → 待たせている時間が長い順（since が古い順）→ 受付番号の昇順
- 対応済みの判定（設計書 docs/dev/dashboard_stats.md §3.4）
  名簿の理由: その受付の最新の対面対応メモが updated_at 以降にあれば対応済み（内容が変わると再び未対応）
  相談: chat_logs.status='open' かつ kind が request／trouble／emergency のものだけ未対応
  答えられなかった質問（outcome='none'）は入れない（案内・お知らせで解決するもの。避難者の声カードに残す）
"""
from __future__ import annotations

from datetime import datetime

from . import cases, checkin, db
from .config import now
from .schema.standard_form import CARE, NEEDS, NEEDS_URGENCY, REL

LEVEL_LABEL = {3: "至急", 2: "今日中", 1: "通常"}   # 3 段（赤・黄・緑）
# 要配慮区分のレベル（本人・同行者のどちらでも同じ）。医療機器は至急、病気・妊産婦・要介護は今日中
CARE_LEVEL = {"MED": 3, "ILL": 2, "PRG": 2, "NUR": 2, "DIS": 1, "INF": 1, "ALG": 1}
ELDERLY_LEVEL_AGE = 75   # ai_staff._needs_care と同じ線
ELDERLY_LEVEL = 1
# 相談の種類 → 理由の見出し。緊急（ガード）は 3、話題が医療の要望・困りごとは 2、その他は 1
CHAT_KINDS = {"emergency": "緊急の相談", "request": "要望", "trouble": "困りごと"}
CHAT_EMERGENCY_LEVEL = 3
CHAT_MEDICAL_LEVEL = 2
CHAT_OTHER_LEVEL = 1
TOP_N = 10   # ダッシュボード・eei.json に出す件数


def to_level(urgency5: int) -> int:
    """5 段の緊急度（standard_form.NEEDS_URGENCY）を 3 段のレベルに写す。
    5・4（透析・酸素・薬・診察）→ 3 至急／3（粉ミルク）→ 2 今日中／2・1 → 1 通常"""
    return 3 if urgency5 >= 4 else 2 if urgency5 == 3 else 1


def _care_label(code: str) -> str:
    return CARE.get(code, code).split("（")[0]   # 「医療機器（透析・酸素等）」→「医療機器」


def _who(m: dict) -> str:
    rel = REL.get(m.get("rel") or "", "")
    return f"同行 {m['name']}（{rel}）" if rel else f"同行 {m['name']}"


def _roster_reasons(ev: dict) -> list[dict]:
    """名簿（伝えたいこと・要配慮・75 歳以上）から出る理由。"""
    out = [{"code": f"need:{n}", "label": NEEDS.get(n, n), "level": to_level(NEEDS_URGENCY.get(n, 1)), "who": "本人"}
           for n in ev["needs"]]
    for p, who in [(ev, "本人"), *((m, _who(m)) for m in ev["members"])]:
        for c in p["care"]:
            if c in CARE_LEVEL:
                out.append({"code": f"care:{c}", "label": _care_label(c), "level": CARE_LEVEL[c], "who": who})
        if p.get("age") is not None and p["age"] >= ELDERLY_LEVEL_AGE:
            out.append({"code": f"age:{ELDERLY_LEVEL_AGE}", "label": f"{ELDERLY_LEVEL_AGE}歳以上（{p['age']}歳）",
                        "level": ELDERLY_LEVEL, "who": who})
    return out


def _chat_reason(c: dict, who: str) -> dict:
    if c["kind"] == "emergency":
        level = CHAT_EMERGENCY_LEVEL
    elif c["topic"] == "medical":
        level = CHAT_MEDICAL_LEVEL
    else:
        level = CHAT_OTHER_LEVEL
    text = (c["content"] or "").strip().replace("\n", " ")
    text = text[:40] + ("…" if len(text) > 40 else "")
    return {"code": f"chat:{c['kind']}", "label": f"{CHAT_KINDS[c['kind']]}: {text}", "level": level,
            "who": who, "log_id": c["id"], "kind_label": CHAT_KINDS[c["kind"]], "text": c["content"] or "",
            "answer": _answer_of(c), "created_at": c["created_at"]}


def _answer_of(c: dict) -> str:
    """相談の直後の AI の答え（同じセッションの次の assistant 行）。展開表示用。"""
    if not c.get("session"):
        return ""
    r = db.query_one("SELECT content FROM chat_logs WHERE session=? AND role='assistant' AND id>? ORDER BY id LIMIT 1",
                     (c["session"], c["id"]))
    return (r or {}).get("content") or ""


def _case_reason(k: dict) -> dict:
    text = (k["text"] or "").strip().replace("\n", " ")
    text = text[:40] + ("…" if len(text) > 40 else "")
    label = f"伝える: {cases.CATEGORIES.get(k['category'], k['category'])}" + (" 急ぎ" if k["urgent"] else "")
    if k["status"] in ("in_progress", "hold"):   # 対応中・保留は状態を添える（/me の表示と同じ言葉）
        label += f"（{cases.STATUSES[k['status']]}）"
    return {"code": f"case:{k['category']}", "label": f"{label} {text}".strip(), "level": k["level"],
            "who": "本人", "case_id": k["id"], "case_status": k["status"],
            "text": k["text"] or "", "category_label": cases.CATEGORIES.get(k["category"], k["category"]),
            "urgent": bool(k["urgent"]), "created_at": k["created_at"]}


def _waiting_min(since: str, t: datetime) -> int:
    try:
        return max(0, int((t - datetime.fromisoformat(since)).total_seconds() // 60))
    except (TypeError, ValueError):
        return 0


def _row(**kw) -> dict:
    reasons = sorted(kw["reasons"], key=lambda x: -x["level"])   # 表示順＝レベル降順（同じレベルは出た順）
    level = reasons[0]["level"]
    return {"id": kw.get("id"), "log_id": kw.get("log_id"), "name": kw["name"], "hh": kw.get("hh"),
            "level": level, "level_label": LEVEL_LABEL[level], "reasons": reasons,
            "since": kw["since"], "waiting_min": 0, "note": kw.get("note") or "",
            "action_url": kw["action_url"],
            "case_ids": [x["case_id"] for x in reasons if x.get("case_id")],   # 「済み」ボタン用
            "cases": [{"id": x["case_id"], "status": x["case_status"]} for x in reasons if x.get("case_id")]}


def items(level: int | None = None) -> list[dict]:
    """対応を待っている行（全件・並べ済み）。level を渡すとそのレベルだけ。"""
    evs = {r["id"]: checkin.to_view(r) for r in db.query("SELECT * FROM evacuees ORDER BY id")}
    last_contact = {r["evacuee_id"]: r["last"] for r in db.query(
        "SELECT evacuee_id, MAX(created_at) AS last FROM contacts GROUP BY evacuee_id")}
    marks = ",".join("?" * len(CHAT_KINDS))
    chats = db.query(
        "SELECT id, session, evacuee_id, kind, topic, content, created_at FROM chat_logs"
        f" WHERE role='user' AND status='open' AND kind IN ({marks}) ORDER BY id", list(CHAT_KINDS))

    by_ev: dict[int, list[dict]] = {}
    rows: list[dict] = []
    for c in chats:
        ev = evs.get(c["evacuee_id"]) if c["evacuee_id"] is not None else None
        if ev is None:   # 匿名端末（または名簿から消えた受付番号）→ 発言 1 件 1 行
            rows.append(_row(log_id=c["id"], name="匿名", reasons=[_chat_reason(c, "匿名")],
                             since=c["created_at"],
                             action_url=f"/staff/cases?kind={c['kind']}&vstatus=open&hours=0#voices-list"))
        elif ev["active"]:   # 退所済みの人の相談は出さない
            by_ev.setdefault(ev["id"], []).append(c)
    case_by_ev: dict[int, list[dict]] = {}
    for k in cases.open_cases():   # 「避難所に伝える」は登録済みの端末だけなので必ず本人にひも付く
        case_by_ev.setdefault(k["evacuee_id"], []).append(k)

    for eid, ev in evs.items():
        if not ev["active"]:
            continue
        reasons: list[dict] = []
        times: list[str] = []
        last = last_contact.get(eid)
        if not last or last < ev["updated_at"]:   # 登録内容が変わってから対面対応のメモが無い
            rr = _roster_reasons(ev)
            if rr:
                reasons += rr
                times.append(ev["updated_at"])
        for c in by_ev.get(eid, []):
            reasons.append(_chat_reason(c, "本人"))
            times.append(c["created_at"])
        for k in case_by_ev.get(eid, []):
            reasons.append(_case_reason(k))
            times.append(k["created_at"])
        if reasons:
            rows.append(_row(id=eid, name=ev["name"], hh=ev["hh"], reasons=reasons, since=min(times),
                             note=ev.get("note"), action_url=f"/staff/roster/{eid}#contact"))

    t = now()
    for r in rows:
        r["waiting_min"] = _waiting_min(r["since"], t)
    rows.sort(key=lambda r: (-r["level"], r["since"], r["id"] if r["id"] is not None else 10 ** 9,
                             r["log_id"] or 0))
    if level:
        rows = [r for r in rows if r["level"] == level]
    return rows


def counts(rows: list[dict] | None = None) -> dict:
    """レベル別の未対応件数。rows は items()（絞り込み無し）の結果。"""
    rows = items() if rows is None else rows
    levels = {lv: 0 for lv in LEVEL_LABEL}
    for r in rows:
        levels[r["level"]] += 1
    return {"levels": levels, "open_total": len(rows), "anonymous": sum(1 for r in rows if r["id"] is None),
            "cases_open": sum(len(r["case_ids"]) for r in rows)}   # 「伝える」の未対応（在所中の人の分）


def for_eei(rows: list[dict] | None = None, limit: int = TOP_N) -> dict:
    """本部へ送る形（氏名・備考・相談の本文を入れない。受付番号・理由コード・時刻だけ）。"""
    rows = items() if rows is None else rows
    c = counts(rows)
    top = []
    for r in rows[:limit]:
        codes = list(dict.fromkeys(x["code"] for x in r["reasons"]))
        top.append({"id": r["id"], "level": r["level"], "reasons": codes, "since": r["since"]})
    return {"levels": {str(k): v for k, v in c["levels"].items()}, "open_total": c["open_total"],
            "anonymous": c["anonymous"], "top": top}
