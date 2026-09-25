"""受付: QR ペイロードの解読・検証、二重登録の判定、名簿への登録・退所。"""
from __future__ import annotations

import base64
import json
import re
import unicodedata
import zlib
from datetime import date

from . import cases, db
from .config import now, now_iso
from .schema.standard_form import ALLERGY, CARE, LANGS, MEMBERS_MAX, NEEDS, NOTE_MAX, REL, SEX

PAYLOAD_FMT = "hinanjo-checkin"
_DEV_RE = re.compile(r"^[0-9a-fA-F]{16,64}$")
_DOB_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class PayloadError(ValueError):
    """ペイロードが不正（400 で返す）。"""


def decode_qr_text(text: str) -> dict:
    """QR の文字列 → dict。先頭 `z:` は base64url(deflate(JSON))。"""
    text = (text or "").strip()
    if text.startswith("z:"):
        raw = text[2:]
        raw += "=" * (-len(raw) % 4)
        try:
            blob = base64.urlsafe_b64decode(raw)
        except Exception as e:  # noqa: BLE001
            raise PayloadError(f"z: の base64 を読めません: {e}") from e
        for wbits in (zlib.MAX_WBITS, -zlib.MAX_WBITS):  # zlib 形式・raw deflate の両方
            try:
                text = zlib.decompress(blob, wbits).decode("utf-8")
                break
            except Exception:  # noqa: BLE001
                continue
        else:
            raise PayloadError("z: の deflate を展開できません")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise PayloadError("避難所受付用の QR ではありません（JSON ではない）") from e
    if not isinstance(data, dict):
        raise PayloadError("ペイロードが JSON オブジェクトではありません")
    return data


def _s(v, limit: int = 200) -> str:
    return str(v).strip()[:limit] if v is not None else ""


def _dob(src: dict, who: str = "") -> str | None:
    """生年月日（YYYY-MM-DD）。無ければ年齢から「その年の1月1日」を作る（年齢計算用の目安）。"""
    dob = _s(src.get("dob"), 10) or None
    if dob:
        if not _DOB_RE.match(dob):
            raise PayloadError(f"{who}生年月日は YYYY-MM-DD で指定してください")
        try:
            date.fromisoformat(dob)
        except ValueError as e:
            raise PayloadError(f"{who}生年月日が不正です") from e
        return dob
    if src.get("age") not in (None, ""):
        try:
            age = int(src["age"])
        except (TypeError, ValueError) as e:
            raise PayloadError(f"{who}年齢は整数で指定してください") from e
        if not 0 <= age <= 120:
            raise PayloadError(f"{who}年齢は 0〜120 で指定してください")
        return f"{now().year - age:04d}-01-01"
    return None


def _care_alg(src: dict) -> tuple[list[str], list[str]]:
    care = [c for c in (src.get("care") or []) if c in CARE]
    alg = [a for a in (src.get("alg") or []) if a in ALLERGY]
    if alg and "ALG" not in care:
        care.append("ALG")
    return care, alg


def _members(raw) -> list[dict]:
    """同行者（家族）の一覧。名前が空の行は飛ばす（フォームの空欄）。"""
    if not isinstance(raw, list):
        return []
    out = []
    for m in raw:
        if not isinstance(m, dict):
            continue
        name = _s(m.get("name"), 60)
        if not name:
            continue
        sex = _s(m.get("sex") or "X").upper()
        rel = _s(m.get("rel"), 16)
        care, alg = _care_alg(m)
        out.append({
            "name": name,
            "kana": _s(m.get("kana"), 60),
            "rel": rel if rel in REL else ("other" if rel else ""),
            "sex": sex if sex in SEX else "X",
            "dob": _dob(m, f"同行者「{name}」の"),
            "care": sorted(set(care), key=list(CARE).index),
            "alg": sorted(set(alg), key=list(ALLERGY).index),
        })
    if len(out) > MEMBERS_MAX:
        raise PayloadError(f"同行者は {MEMBERS_MAX} 人までです")
    return out


def normalize(payload: dict | str, *, require_dev: bool) -> dict:
    """§6.1 のペイロードを検証して正規化する。不正なら PayloadError。"""
    if isinstance(payload, str):
        payload = decode_qr_text(payload)
    if not isinstance(payload, dict):
        raise PayloadError("payload がありません")

    fmt = payload.get("fmt", PAYLOAD_FMT)
    if fmt != PAYLOAD_FMT:
        raise PayloadError(f"形式 fmt={fmt!r} には対応していません")
    v = payload.get("v", 1)
    if v != 1:
        raise PayloadError(f"ペイロードの版 v={v!r} には対応していません")

    dev = _s(payload.get("dev"), 64) or None
    if dev and not _DEV_RE.match(dev):
        raise PayloadError("dev は 16 桁以上の 16 進数で指定してください")
    if require_dev and not dev:
        raise PayloadError("QR に端末ID（dev）がありません")

    name = _s(payload.get("name"), 60)
    if not name:
        raise PayloadError("氏名は必須です")
    addr = _s(payload.get("addr"), 200)

    sex = _s(payload.get("sex") or "X").upper()
    if sex not in SEX:
        raise PayloadError("性別は M / F / X のいずれかです")

    dob = _dob(payload)

    try:
        hh = int(payload.get("hh") or 1)
        pet = int(payload.get("pet") or 0)
    except (TypeError, ValueError) as e:
        raise PayloadError("hh / pet は整数で指定してください") from e
    members = _members(payload.get("members"))
    # 人数は「本人＋登録した同行者」より少なくしない（名前を登録しない同行者がいれば hh の方が多い）
    hh = max(1, min(max(hh, 1 + len(members)), 30))
    pet = max(0, min(pet, 20))

    care, alg = _care_alg(payload)
    needs = [n for n in (payload.get("needs") or []) if n in NEEDS]
    # 伝えたいことから分かる要配慮は区分にも反映する（透析・酸素 → 医療機器）
    if ("dialysis" in needs or "oxygen" in needs) and "MED" not in care:
        care.append("MED")

    lang = _s(payload.get("lang") or "ja", 8).lower()
    if lang not in LANGS:
        lang = lang or "ja"

    return {
        "dev": dev,
        "name": name,
        "kana": _s(payload.get("kana"), 60),
        "addr": addr,
        "sex": sex,
        "dob": dob,
        "hh": hh,
        "care": sorted(set(care), key=list(CARE).index),
        "alg": sorted(set(alg), key=list(ALLERGY).index),
        "needs": sorted(set(needs), key=list(NEEDS).index),
        "note": _s(payload.get("note"), NOTE_MAX),
        "lang": lang,
        "pet": pet,
        "members": members,
    }


# --- 名簿 ---------------------------------------------------------------
def age_of(dob: str | None) -> int | None:
    if not dob:
        return None
    try:
        b = date.fromisoformat(dob)
    except ValueError:
        return None
    t = now().date()
    return t.year - b.year - ((t.month, t.day) < (b.month, b.day))


def to_view(row: dict) -> dict:
    """DB 行 → 画面・API 用（JSON 配列の展開・年齢）。"""
    r = dict(row)
    r["care"] = json.loads(r.get("care") or "[]")
    r["alg"] = json.loads(r.get("alg") or "[]")
    r["needs"] = json.loads(r.get("needs") or "[]")
    r["members"] = [{**m, "age": age_of(m.get("dob"))} for m in json.loads(r.get("members") or "[]")]
    r["age"] = age_of(r.get("dob"))
    r["active"] = not r.get("checked_out_at")
    return r


def get(evacuee_id: int) -> dict | None:
    row = db.query_one("SELECT * FROM evacuees WHERE id=?", (evacuee_id,))
    return to_view(row) if row else None


def _key(s: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or "")).lower()


def find_duplicate(p: dict) -> dict | None:
    """同一人物の既存登録を探す。dev が一致すれば確定。dev が無ければ氏名＋（生年月日 or 住所）。"""
    if p.get("dev"):
        row = db.query_one("SELECT * FROM evacuees WHERE dev=?", (p["dev"],))
        return to_view(row) if row else None
    name = _key(p["name"])
    for row in db.query("SELECT * FROM evacuees WHERE checked_out_at IS NULL"):
        if _key(row["name"]) != name:
            continue
        if (p.get("dob") and row["dob"] == p["dob"]) or (
            p.get("addr") and _key(row["addr"]) == _key(p["addr"])
        ):
            return to_view(row)
    return None


def register(p: dict, source: str, staff_note: str | None = None) -> tuple[str, dict]:
    """登録する。戻り値 (status, evacuee)。status は created / duplicate / updated。

    - 在所中の同一人物 → duplicate（何も変えない）
    - 退所済みの同一人物（dev 一致）→ updated（再入所。内容を最新にする）
    """
    ts = now_iso()
    dup = find_duplicate(p)
    if dup and dup["active"]:
        return "duplicate", dup
    fields = (
        p["name"], p["kana"], p["addr"], p["sex"], p["dob"], p["hh"],
        json.dumps(p["care"]), json.dumps(p["alg"]), json.dumps(p.get("needs") or []),
        p["note"], p["lang"], p["pet"], json.dumps(p.get("members") or [], ensure_ascii=False),
    )
    if dup:  # 退所済みの再入所
        db.execute(
            "UPDATE evacuees SET name=?, kana=?, addr=?, sex=?, dob=?, hh=?, care=?, alg=?, needs=?,"
            " note=?, lang=?, pet=?, members=?, source=?, staff_note=COALESCE(?, staff_note),"
            " checked_in_at=?, checked_out_at=NULL, updated_at=? WHERE id=?",
            (*fields, source, staff_note, ts, ts, dup["id"]),
        )
        return "updated", get(dup["id"])
    new_id = db.execute(
        "INSERT INTO evacuees(name, kana, addr, sex, dob, hh, care, alg, needs, note, lang, pet, members,"
        " dev, source, staff_note, checked_in_at, created_at, updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (*fields, p["dev"], source, staff_note, ts, ts, ts),
    )
    return "created", get(new_id)


def update(evacuee_id: int, p: dict, staff_note: str | None, *, keep_staff_note: bool = False) -> dict | None:
    """登録内容を書き換える。keep_staff_note=True（本人がスマホから直すとき）は運営メモに触らない。"""
    db.execute(
        "UPDATE evacuees SET name=?, kana=?, addr=?, sex=?, dob=?, hh=?, care=?, alg=?, needs=?,"
        " note=?, lang=?, pet=?, members=?, staff_note=?, updated_at=? WHERE id=?",
        (
            p["name"], p["kana"], p["addr"], p["sex"], p["dob"], p["hh"],
            json.dumps(p["care"]), json.dumps(p["alg"]), json.dumps(p.get("needs") or []),
            p["note"], p["lang"], p["pet"], json.dumps(p.get("members") or [], ensure_ascii=False),
            (db.query_one("SELECT staff_note FROM evacuees WHERE id=?", (evacuee_id,)) or {}).get("staff_note")
            if keep_staff_note else staff_note,
            now_iso(), evacuee_id,
        ),
    )
    return get(evacuee_id)


# --- 対面対応のメモ（バーコードを読んでから書く） ---------------------------
def add_contact(evacuee_id: int, note: str, close: bool = True) -> int:
    """対面対応のメモを残す。close=True ならその人の未対応の相談・伝言も対応済みにする（対応の優先順から1回の操作で消える）。
    close=False は「記録だけ」（相談・伝言は開いたまま）。

    evacuees.updated_at は進めない（「メモが updated_at 以降にあれば対応済み」で判定するため。priority.py）。
    """
    note = _s(note, 500)
    if not note:
        raise PayloadError("メモが空です")
    ts = now_iso()
    with db.conn() as c:
        cid = c.execute("INSERT INTO contacts(evacuee_id, note, created_at) VALUES(?,?,?)",
                        (evacuee_id, note, ts)).lastrowid
        if close:
            c.execute("UPDATE chat_logs SET status='done', handled_at=?"
                      " WHERE evacuee_id=? AND role='user' AND status='open'", (ts, evacuee_id))
            cases.close_open_for(evacuee_id, ts, c)   # 「避難所に伝える」の未対応も対応済みに
    return cid


def contacts(evacuee_id: int) -> list[dict]:
    return db.query("SELECT * FROM contacts WHERE evacuee_id=? ORDER BY id DESC", (evacuee_id,))


def chats(evacuee_id: int, limit: int = 30) -> list[dict]:
    """その人のチャット（発言と、その直後の答え）。運営画面の個人ページ用。"""
    rows = db.query(
        "SELECT * FROM chat_logs WHERE evacuee_id=? AND role IN ('user','assistant') ORDER BY id DESC LIMIT ?",
        (evacuee_id, limit * 2))
    out, pending_answer = [], None
    for r in rows:  # 新しい順に来るので、答え → 発言 の順に並ぶ
        if r["role"] == "assistant":
            pending_answer = r["content"]
        else:
            out.append({**r, "answer": pending_answer})
            pending_answer = None
    return out[:limit]


def chat_counts() -> dict[int, dict]:
    """受付番号 → {total, open}（名簿の一覧用）。"""
    out: dict[int, dict] = {}
    for r in db.query("SELECT evacuee_id AS id, COUNT(*) AS total,"
                      " SUM(CASE WHEN status='open' AND kind IN ('request','trouble','emergency') THEN 1 ELSE 0 END) AS open"
                      " FROM chat_logs WHERE role='user' AND evacuee_id IS NOT NULL GROUP BY evacuee_id"):
        out[r["id"]] = {"total": r["total"], "open": r["open"] or 0}
    return out


def checkout(evacuee_id: int) -> dict | None:
    ts = now_iso()
    db.execute(
        "UPDATE evacuees SET checked_out_at=COALESCE(checked_out_at, ?), updated_at=? WHERE id=?",
        (ts, ts, evacuee_id),
    )
    return get(evacuee_id)


def search(q: str = "", care: str = "", status: str = "active") -> list[dict]:
    sql = "SELECT * FROM evacuees WHERE 1=1"
    params: list = []
    if status == "active":
        sql += " AND checked_out_at IS NULL"
    elif status == "out":
        sql += " AND checked_out_at IS NOT NULL"
    if care:
        sql += " AND care LIKE ?"
        params.append(f'%"{care}"%')
    rows = [to_view(r) for r in db.query(sql + " ORDER BY id DESC", params)]
    if q:
        k = _key(q)
        rows = [
            r for r in rows
            if k in _key(r["name"]) or k in _key(r["kana"] or "") or k in _key(r["addr"] or "")
            or k == str(r["id"])
            or any(k in _key(m["name"]) or k in _key(m.get("kana") or "") for m in r["members"])
        ]
    return rows
