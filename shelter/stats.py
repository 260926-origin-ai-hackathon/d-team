"""在所者の集計・不足物資の見積もり（ルール計算）・名簿 CSV・EEI 集計 JSON。

数え方（受付1件＝代表者1人＋同行者 hh-1 人。同行者は members に1人ずつ登録できる）:
- 在所人数 = 在所中の受付の hh の合計。世帯数 = 在所中の受付件数
- 性別・年齢・要配慮・アレルギーは、代表者と「登録した同行者」を1人ずつ数える
  名前を登録していない同行者（hh − 1 − 登録した同行者）は性別「不明」に数える。言語は受付（世帯）単位
- 乳幼児 = 3歳未満の人。その世帯に3歳未満の登録が無く、代表者が「乳幼児同伴(INF)」なら1人と数える
"""
from __future__ import annotations

import csv
import io
import math
import re
import unicodedata
from collections import Counter

from . import cases, checkin, db, priority
from .config import now_iso
from .schema.standard_form import (ALLERGY, CARE, LANGS, NEEDS, REL, ROSTER_CARE_COLUMNS, ROSTER_COLUMNS, SEX,
                                   SOURCE)


def active_evacuees() -> list[dict]:
    return [checkin.to_view(r) for r in db.query(
        "SELECT * FROM evacuees WHERE checked_out_at IS NULL ORDER BY id")]


def people_of(r: dict) -> list[dict]:
    """受付1件の「登録した人」（代表者＋同行者）。集計・要配慮の洗い出しで使う。"""
    return [r, *r.get("members", [])]


def summary(rows: list[dict] | None = None) -> dict:
    rows = active_evacuees() if rows is None else rows
    total = sum(r["hh"] for r in rows)
    people = [p for r in rows for p in people_of(r)]
    unregistered = sum(max(0, r["hh"] - len(people_of(r))) for r in rows)  # 名前を登録していない同行者
    sex = Counter(p["sex"] for p in people)
    companions = total - len(rows)
    care = Counter(c for p in people for c in p["care"])
    alg = Counter(a for p in people for a in p["alg"])
    langs = Counter(r["lang"] or "ja" for r in rows)
    infants = 0
    for r in rows:
        n = sum(1 for p in people_of(r) if p["age"] is not None and p["age"] < 3)
        infants += n or (1 if "INF" in r["care"] else 0)
    elderly = sum(1 for p in people if p["age"] is not None and p["age"] >= 65)
    return {
        "total": total,
        "households": len(rows),
        "male": sex.get("M", 0),
        "female": sex.get("F", 0),
        "unknown": sex.get("X", 0) + unregistered,
        "companions": companions,
        "registered_companions": len(people) - len(rows),
        "infants": infants,
        "elderly": elderly,
        "care": {k: care.get(k, 0) for k in CARE},
        "care_people": sum(1 for p in people if p["care"]),
        "allergy": {k: alg.get(k, 0) for k in ALLERGY if alg.get(k)},
        "languages": dict(langs),
        "pets": sum(r["pet"] for r in rows),
        "checked_out": (db.query_one(
            "SELECT COUNT(*) AS n FROM evacuees WHERE checked_out_at IS NOT NULL") or {"n": 0})["n"],
    }


# --- 内訳（想定地区・日本語以外・支援が要る世帯・受付の方法。設計書 docs/dev/dashboard_stats.md §4） ---
def _norm(s: str) -> str:
    """住所・町名の突き合わせ用（全角半角をそろえ、空白を落とし、小文字に）。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or "")).lower()


def area_towns() -> list[str]:
    """設定の想定地区（カンマ・読点区切り）。"""
    raw = db.get_setting("area_towns") or ""
    return [t.strip() for t in re.split(r"[,、，]", raw) if t.strip()]


def _in_area(addr: str | None, towns: list[str]) -> str:
    """in（想定地区）／out（想定地区外）／unknown（住所なし）。"""
    a = _norm(addr or "")
    if not a:
        return "unknown"
    return "in" if any(_norm(t) and _norm(t) in a for t in towns) else "out"


def breakdown(rows: list[dict] | None = None, towns: list[str] | None = None) -> dict:
    """内訳の自動集計。人数は hh の合計（summary と同じ数え方）。towns を省くと設定の想定地区。"""
    rows = active_evacuees() if rows is None else rows
    towns = area_towns() if towns is None else towns
    area = {k: {"households": 0, "people": 0} for k in ("in", "out", "unknown")}
    for r in rows:
        a = area[_in_area(r["addr"], towns)]
        a["households"] += 1
        a["people"] += r["hh"]
    total = sum(r["hh"] for r in rows)

    # 日本語以外: 世帯の言語が日本語以外、または伝えたいことに「日本語がわからない」（世帯単位・二重に数えない）
    nj = [r for r in rows if (r["lang"] or "ja") != "ja" or "language" in r["needs"]]
    by_lang: Counter = Counter()
    for r in nj:
        by_lang[r["lang"] if (r["lang"] or "ja") != "ja" else "unknown"] += r["hh"]

    by_need = Counter(n for r in rows for n in r["needs"])
    source = Counter(r["source"] for r in rows)
    return {
        "area": {"towns": towns, **area,
                 "in_pct": math.floor(area["in"]["people"] * 100 / total + 0.5) if total else None},  # 人数ベース・四捨五入
        "non_japanese": {"households": len(nj), "people": sum(r["hh"] for r in nj), "by_lang": dict(by_lang)},
        "needs": {
            "households": sum(1 for r in rows if r["needs"] or any(p["care"] for p in people_of(r))),
            "people_with_care": sum(1 for r in rows for p in people_of(r) if p["care"]),
            "by_need": {k: by_need.get(k, 0) for k in NEEDS},
        },
        "source": {k: source.get(k, 0) for k in SOURCE},
    }


# --- 不足物資（ルール計算。AI はコメントだけ） -----------------------------
# (キー, 品目, 単位, 1日の必要量の計算, 根拠)
SUPPLY_RULES = [
    ("water", "飲料水", "L/日", lambda s: 3 * s["total"], "1人1日3L"),
    ("meals", "食事", "食/日", lambda s: 3 * s["total"], "1人1日3食"),
    ("blankets", "毛布", "枚", lambda s: s["total"], "1人1枚"),
    ("toilets", "簡易トイレ", "基", lambda s: math.ceil(s["total"] / 20) if s["total"] else 0,
     "20人に1基（内閣府 2024 ガイドライン）"),
    ("milk", "粉ミルク・液体ミルク", "人分/日", lambda s: s["infants"], "乳幼児の数"),
    ("diapers", "乳幼児用おむつ", "枚/日", lambda s: 8 * s["infants"], "乳幼児1人1日8枚"),
    ("sanitary", "生理用品", "人分", lambda s: s["female"], "女性の数（登録した同行者を含む）"),
    ("allergy_meals", "アレルギー対応食", "食/日",
     lambda s: 3 * s["care"].get("ALG", 0), "アレルギーのある人×3食"),
    ("adult_diapers", "大人用おむつ", "枚/日", lambda s: 5 * s["care"].get("NUR", 0),
     "要介護の人1人1日5枚（目安）"),
]


def supplies(s: dict | None = None) -> list[dict]:
    s = summary() if s is None else s
    out = []
    for key, item, unit, fn, basis in SUPPLY_RULES:
        need = int(fn(s))
        stock_raw = db.get_setting(f"stock_{key}")
        stock = int(stock_raw) if stock_raw not in (None, "") else None
        short = max(0, need - stock) if stock is not None else None
        out.append({"key": key, "item": item, "unit": unit, "need": need,
                    "stock": stock, "short": short, "basis": basis})
    return out


# --- エクスポート -------------------------------------------------------
def _yn(v: bool) -> str:
    return "○" if v else ""


def roster_rows(include_checked_out: bool = True) -> list[list]:
    sql = "SELECT * FROM evacuees" + ("" if include_checked_out else " WHERE checked_out_at IS NULL")
    rows = []
    for r in (checkin.to_view(x) for x in db.query(sql + " ORDER BY id")):
        rec = {
            "受付番号": r["id"], "入所日時": r["checked_in_at"], "退所日時": r["checked_out_at"] or "",
            "氏名": r["name"], "ふりがな": r["kana"] or "", "性別": SEX.get(r["sex"], ""),
            "生年月日": r["dob"] or "", "年齢": "" if r["age"] is None else r["age"],
            "住所": r["addr"] or "", "世帯人数": r["hh"],
            "アレルギー内訳": "・".join(ALLERGY[a] for a in r["alg"]),
            "備考": r["note"] or "",
            "運営に伝えたいこと(拡張)": "・".join(NEEDS.get(n, n) for n in r["needs"]),
            "使用言語(拡張)": LANGS.get(r["lang"], r["lang"] or ""),
            "ペット(拡張)": r["pet"], "受付方法": SOURCE.get(r["source"], r["source"]),
            "端末ID": r["dev"] or "", "続柄(拡張)": "本人",
        }
        for col, code in ROSTER_CARE_COLUMNS.items():
            rec[col] = _yn(code in r["care"])
        rows.append([rec[c] for c in ROSTER_COLUMNS])
        for m in r["members"]:  # 同行者は同じ受付番号で1人1行
            mrec = {**rec, "氏名": m["name"], "ふりがな": m.get("kana") or "", "性別": SEX.get(m["sex"], ""),
                    "生年月日": m.get("dob") or "", "年齢": "" if m["age"] is None else m["age"],
                    "アレルギー内訳": "・".join(ALLERGY[a] for a in m["alg"]), "備考": "",
                    "運営に伝えたいこと(拡張)": "", "ペット(拡張)": "", "端末ID": "",
                    "続柄(拡張)": REL.get(m.get("rel") or "", "同行者")}
            for col, code in ROSTER_CARE_COLUMNS.items():
                mrec[col] = _yn(code in m["care"])
            rows.append([mrec[c] for c in ROSTER_COLUMNS])
    return rows


def roster_csv() -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(ROSTER_COLUMNS)
    w.writerows(roster_rows())
    return ("﻿" + buf.getvalue()).encode("utf-8")  # BOM 付き（Excel 用）


def eei() -> dict:
    """EEI「避難所等」に合わせた集計値（本部へ送る。個人情報を含まない）。

    area 以降は拡張（v1.7）。対応の優先順は受付番号・理由コード・時刻だけ（氏名・備考・相談の本文は入れない）。
    """
    p = db.profile()
    rows = active_evacuees()
    s = summary(rows)
    b = breakdown(rows)
    a = b["area"]
    return {
        "v": 1,
        "shelter": {"name": p["name"], "address": p["address"]},
        "as_of": now_iso(),
        "open": True,
        "evacuees": {"total": s["total"], "male": s["male"], "female": s["female"],
                     "unknown": s["unknown"]},
        "households": s["households"],
        "infants": s["infants"],
        "elderly": s["elderly"],
        "care": s["care"],
        "allergy": s["allergy"],
        "languages": s["languages"],
        "pets": s["pets"],
        "shortages": [
            {"item": x["item"], "need": x["need"], "unit": x["unit"],
             **({"stock": x["stock"], "short": x["short"]} if x["stock"] is not None else {})}
            for x in supplies(s) if x["need"] > 0
        ],
        "area": {"towns": a["towns"], "in_households": a["in"]["households"], "in_people": a["in"]["people"],
                 "out_people": a["out"]["people"], "unknown_people": a["unknown"]["people"],
                 "in_pct": a["in_pct"]},
        "non_japanese": b["non_japanese"],
        "needs": {"households": b["needs"]["households"], "by_need": b["needs"]["by_need"]},
        "priority": priority.for_eei(),
        "cases": cases.counts_for_eei(),   # 「避難所に伝える」の分類 × 状態の件数だけ（本文・氏名なし）
    }
