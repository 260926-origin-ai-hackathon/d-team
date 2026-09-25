"""案件（いまは避難者の「避難所に伝える」だけ）。docs/dev/ops_console_plan.md §2・§4 の最小版。

- 登録済みの端末だけが送れる（本人にひも付ける。匿名は受けない）
- レベルは 3 段（priority.LEVEL_LABEL と同じ）。医療・体調は 3、安全・不安と乳幼児・妊産婦は 2、それ以外は 1。
  「急ぐ」は 1 段上げる（上限 3）。暴力・急病の言葉（ai_evacuee.guard_kind）に当たる本文は 3
- 状態は 4 つ: open（受付済）→ in_progress（対応中）→ hold（保留）→ done（対応済）。done で handled_at を入れる
- 運営の対面対応メモ（checkin.add_contact）を書くと、その人の open な案件も done（相談と同じ扱い）
- AI は使わない
"""
from __future__ import annotations

from . import db
from .config import now_iso

SOURCE_MESSAGE = "message"
# 統一分類 8 種（§2.2）
CATEGORIES = {"medical": "医療・体調", "mobility": "介助・移動", "baby": "乳幼児・妊産婦", "food": "食事・水",
              "environment": "生活環境", "safety": "安全・不安", "family": "家族・安否", "other": "その他・手続き"}
CATEGORIES_EN = {"medical": "Health / medical", "mobility": "Assistance / mobility", "baby": "Babies / pregnancy",
                 "food": "Food / water", "environment": "Living conditions", "safety": "Safety / worries",
                 "family": "Family / missing persons", "other": "Other / paperwork"}
CATEGORY_LEVEL = {"medical": 3, "safety": 2, "baby": 2}   # それ以外は 1
STATUSES = {"open": "受付済", "in_progress": "対応中", "hold": "保留", "done": "対応済"}
STATUSES_EN = {"open": "Received", "in_progress": "In progress", "hold": "On hold", "done": "Done"}
OPEN_STATUSES = ("open", "in_progress", "hold")
TEXT_MAX = 200


class CaseError(ValueError):
    pass


def level_of(category: str, urgent: bool, guarded: bool = False) -> int:
    if guarded:
        return 3
    return min(3, CATEGORY_LEVEL.get(category, 1) + (1 if urgent else 0))


def create(evacuee_id: int, category: str, urgent: bool, text: str, lang: str,
           guarded: bool = False) -> int:
    """「避難所に伝える」を 1 件立てる。guarded は本文が暴力・急病のガードに当たったとき（レベル 3）。"""
    if category not in CATEGORIES:
        raise CaseError("用件を 1 つ選んでください")
    text = (text or "").strip()[:TEXT_MAX]
    ts = now_iso()
    return db.execute(
        "INSERT INTO cases(evacuee_id, source, category, urgent, text, lang, level, status, created_at, updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (evacuee_id, SOURCE_MESSAGE, category, 1 if urgent else 0, text, "en" if lang == "en" else "ja",
         level_of(category, urgent, guarded), "open", ts, ts))


def submit(evacuee_id: int, category: str, urgent: bool, text: str, lang: str,
           guarded: bool = False) -> tuple[int, str]:
    """/tell から。同じ人・同じ用件の案件があれば新規にせず、それを最新にする。

    - 未対応（open / in_progress / hold）: 状態はそのまま、本文・急ぐ・レベルを最新に
    - 対応済（done）: open に戻す（再オープン）
    - 無ければ新規。戻り値は (案件 id, "new" / "updated" / "reopened")
    """
    if category not in CATEGORIES:
        raise CaseError("用件を 1 つ選んでください")
    old = db.query_one("SELECT * FROM cases WHERE evacuee_id=? AND category=? ORDER BY id DESC LIMIT 1",
                       (evacuee_id, category))
    if not old:
        return create(evacuee_id, category, urgent, text, lang, guarded), "new"
    text = (text or "").strip()[:TEXT_MAX] or old["text"]
    reopened = old["status"] == "done"
    if not reopened:   # 未対応のあいだは急ぎ・レベルを下げない
        urgent = urgent or bool(old["urgent"])
    level = max(level_of(category, urgent, guarded), 1 if reopened else old["level"])
    db.execute("UPDATE cases SET text=?, urgent=?, lang=?, level=?, status=?, handled_at=?, updated_at=? WHERE id=?",
               (text, 1 if urgent else 0, "en" if lang == "en" else "ja", level,
                "open" if reopened else old["status"], None, now_iso(), old["id"]))
    return old["id"], ("reopened" if reopened else "updated")


def list_for(evacuee_id: int, limit: int = 30) -> list[dict]:
    """本人が伝えたこと（新しい順）。"""
    return db.query("SELECT * FROM cases WHERE evacuee_id=? ORDER BY id DESC LIMIT ?", (evacuee_id, limit))


def get(case_id: int) -> dict | None:
    return db.query_one("SELECT * FROM cases WHERE id=?", (case_id,))


def set_status(case_id: int, status: str) -> bool:
    if status not in STATUSES:
        raise CaseError("状態が不正です")
    if not get(case_id):
        return False
    ts = now_iso()
    db.execute("UPDATE cases SET status=?, handled_at=?, updated_at=? WHERE id=?",
               (status, ts if status == "done" else None, ts, case_id))
    return True


def close_open_for(evacuee_id: int, ts: str, c=None) -> None:
    """対面対応メモを書いたとき、その人の未対応の案件を対応済みにする（checkin.add_contact から）。"""
    sql = ("UPDATE cases SET status='done', handled_at=?, updated_at=?"
           f" WHERE evacuee_id=? AND status IN ({','.join('?' * len(OPEN_STATUSES))})")
    params = (ts, ts, evacuee_id, *OPEN_STATUSES)
    if c is not None:
        c.execute(sql, params)
    else:
        db.execute(sql, params)


def open_cases() -> list[dict]:
    """全員の未対応（open / in_progress / hold）の案件。古い順。"""
    marks = ",".join("?" * len(OPEN_STATUSES))
    return db.query(f"SELECT * FROM cases WHERE status IN ({marks}) ORDER BY id", OPEN_STATUSES)


def counts_for_eei() -> dict:
    """本部へ送る形: 分類 × 状態の件数だけ（本文・氏名・受付番号は入れない）。"""
    out = {cat: {st: 0 for st in STATUSES} for cat in CATEGORIES}
    for r in db.query("SELECT category, status, COUNT(*) AS n FROM cases GROUP BY category, status"):
        if r["category"] in out and r["status"] in STATUSES:
            out[r["category"]][r["status"]] = r["n"]
    return out
