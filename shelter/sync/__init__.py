"""ネット復旧時の同期: 名簿 CSV と EEI 集計 JSON を作り、設定した送り先へ送る。

送信のたびに sync_log に残す。file 以外の送り先でも、必ず outbox にも控えを書く（失敗しても手元に残る）。
"""
from __future__ import annotations

import json

from .. import db, stats
from ..config import config, now, now_iso
from .base import SendResult, Transport
from .file import FileTransport
from .http import HttpTransport
from .supabase import SupabaseTransport

TRANSPORTS = {"file": "ファイル（outbox）", "http": "HTTP POST", "supabase": "Supabase（未実装）"}


def make_transport(name: str, batch: str) -> Transport:
    if name == "http":
        return HttpTransport(db.get_setting("sync_url") or "", config.sync_token)
    if name == "supabase":
        return SupabaseTransport(config.supabase_url, config.supabase_key)
    return FileTransport(config.outbox_dir, batch)


def payloads() -> list[tuple[str, str, bytes]]:
    """(kind, filename, content)。送るものは常にこの2つ。"""
    return [
        ("roster", "roster.csv", stats.roster_csv()),
        ("eei", "eei.json", json.dumps(stats.eei(), ensure_ascii=False, indent=2).encode("utf-8")),
    ]


def _log(transport: str, res: SendResult, kind: str) -> int:
    return db.execute(
        "INSERT INTO sync_log(transport, target, payload_kind, status, detail, created_at)"
        " VALUES(?,?,?,?,?,?)",
        (transport, res.target, kind, "ok" if res.ok else "error", res.detail, now_iso()))


def run(transport_name: str | None = None) -> dict:
    name = transport_name or db.get_setting("sync_transport") or "file"
    batch = now().strftime("%Y%m%d-%H%M%S")
    p = db.profile()
    meta = {"shelter": p["name"], "as_of": now_iso()}
    files = payloads()
    sent, log_id = [], 0

    # 控え（file 以外のときも outbox に残す）
    if name != "file":
        backup = FileTransport(config.outbox_dir, batch)
        for kind, fn, content in files:
            backup.send(kind, fn, content, meta)

    try:
        transport = make_transport(name, batch)
    except ValueError as e:
        res = SendResult(False, "-", str(e))
        log_id = _log(name, res, "all")
        return {"transport": name, "sent": [{"kind": "all", "ok": False, "detail": str(e)}],
                "log_id": log_id, "outbox": str(config.outbox_dir / batch)}

    for kind, fn, content in files:
        res = transport.send(kind, fn, content, meta)
        log_id = _log(name, res, kind)
        sent.append({"kind": kind, "file": fn, "ok": res.ok, "target": res.target,
                     "detail": res.detail})
    db.set_setting("last_sync_at", now_iso())
    return {"transport": name, "sent": sent, "log_id": log_id,
            "outbox": str(config.outbox_dir / batch)}


def recent_logs(limit: int = 30) -> list[dict]:
    return db.query("SELECT * FROM sync_log ORDER BY id DESC LIMIT ?", (limit,))
