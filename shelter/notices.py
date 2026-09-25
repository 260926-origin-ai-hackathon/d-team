"""お知らせ: 日本語で投稿 → 英訳を自動生成（編集可）。ja/en の組は group_id で束ねる。"""
from __future__ import annotations

import re

from . import db, llm
from .config import config, now_iso

CATEGORIES = {
    "food": ("配給", "Food"),
    "water": ("給水", "Water"),
    "medical": ("医療", "Medical"),
    "toilet": ("トイレ", "Toilet"),
    "other": ("その他", "Other"),
}


def category_label(cat: str, lang: str = "ja") -> str:
    ja, en = CATEGORIES.get(cat, CATEGORIES["other"])
    return en if lang == "en" else ja


def hhmm(ts: str | None) -> str:
    return ts[11:16] if ts and len(ts) >= 16 else ""


def list_notices(lang: str = "ja", limit: int = 50) -> list[dict]:
    """指定言語のお知らせ（新しい順）。その言語版が無い組は日本語版で埋める。"""
    rows = db.query("SELECT * FROM notices ORDER BY posted_at DESC, id DESC")
    by_group: dict[int, dict] = {}
    order: list[int] = []
    for r in rows:
        g = r["group_id"] or r["id"]
        if g not in by_group:
            order.append(g)
            by_group[g] = r
        elif r["lang"] == lang and by_group[g]["lang"] != lang:
            by_group[g] = r
    out = []
    for g in order:
        r = by_group[g]
        if r["lang"] != lang and r["lang"] != "ja":
            continue
        out.append({**r, "group_id": g, "hhmm": hhmm(r["posted_at"]),
                    "category_label": category_label(r["category"], lang)})
    out.sort(key=lambda r: (r["posted_at"], r["id"]), reverse=True)
    return out[:limit]


def groups(limit: int = 30) -> list[dict]:
    """運営者画面用: 組ごとに ja/en を並べる。"""
    rows = db.query("SELECT * FROM notices ORDER BY posted_at DESC, id DESC")
    out: dict[int, dict] = {}
    for r in rows:
        g = r["group_id"] or r["id"]
        out.setdefault(g, {"group_id": g, "posted_at": r["posted_at"], "category": r["category"]})
        out[g][r["lang"]] = r
    return sorted(out.values(), key=lambda x: x["posted_at"], reverse=True)[:limit]


def post(category: str, title: str, body: str, posted_at: str | None = None,
         author: str = "staff") -> int:
    ts = posted_at or now_iso()
    nid = db.execute(
        "INSERT INTO notices(category, lang, title, body, posted_at, author) VALUES(?,?,?,?,?,?)",
        (category, "ja", title.strip(), body.strip(), ts, author))
    db.execute("UPDATE notices SET group_id=? WHERE id=?", (nid, nid))
    return nid


def save_translation(group_id: int, lang: str, title: str, body: str,
                     machine: bool = False) -> None:
    base = db.query_one("SELECT * FROM notices WHERE id=?", (group_id,))
    if not base:
        return
    cur = db.query_one("SELECT id FROM notices WHERE group_id=? AND lang=?", (group_id, lang))
    if cur:
        db.execute("UPDATE notices SET title=?, body=?, machine_translated=? WHERE id=?",
                   (title.strip(), body.strip(), int(machine), cur["id"]))
    else:
        db.execute(
            "INSERT INTO notices(category, lang, group_id, title, body, posted_at, author,"
            " machine_translated) VALUES(?,?,?,?,?,?,?,?)",
            (base["category"], lang, group_id, title.strip(), body.strip(), base["posted_at"],
             base["author"], int(machine)))


def delete_group(group_id: int) -> None:
    db.execute("DELETE FROM notices WHERE group_id=? OR id=?", (group_id, group_id))


_TRANSLATE_SYS = (
    "You translate notices posted at a Japanese evacuation shelter into plain, short English "
    "for foreign residents. Keep times, dates, places and numbers exactly. Do not add information. "
    "Output exactly two lines:\nTITLE: <english title>\nBODY: <english body>"
)


async def translate_to_en(title: str, body: str) -> tuple[str, str]:
    text = await llm.complete(
        [{"role": "system", "content": _TRANSLATE_SYS},
         {"role": "user", "content": f"TITLE: {title}\nBODY: {body}"}],
        max_tokens=config.max_tokens_staff, temperature=0.1)
    m_t = re.search(r"TITLE:\s*(.+)", text)
    m_b = re.search(r"BODY:\s*(.+)", text, re.DOTALL)
    en_title = (m_t.group(1).strip() if m_t else title)
    en_body = (m_b.group(1).strip() if m_b else text.strip())
    return en_title, en_body


async def post_with_translation(category: str, title: str, body: str,
                                posted_at: str | None = None) -> tuple[int, str | None]:
    """投稿して英訳まで作る。英訳に失敗しても日本語版は残す（戻り値の2つ目がエラー文）。"""
    gid = post(category, title, body, posted_at)
    try:
        en_title, en_body = await translate_to_en(title, body)
        save_translation(gid, "en", en_title, en_body, machine=True)
        return gid, None
    except Exception as e:  # noqa: BLE001
        return gid, f"英訳の自動生成に失敗しました（{type(e).__name__}）。手で入力してください。"
