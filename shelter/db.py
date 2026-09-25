"""SQLite 接続・マイグレーション・設定値。

1プロセス・少人数アクセスなので、呼び出しごとに接続を開く素朴な作り（WAL モード）。
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

from .config import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS evacuees(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  dev TEXT UNIQUE,
  name TEXT NOT NULL, kana TEXT, addr TEXT, sex TEXT, dob TEXT,
  hh INTEGER NOT NULL DEFAULT 1,
  care TEXT NOT NULL DEFAULT '[]', alg TEXT NOT NULL DEFAULT '[]',
  note TEXT, lang TEXT, pet INTEGER NOT NULL DEFAULT 0,
  source TEXT NOT NULL, staff_note TEXT,
  checked_in_at TEXT NOT NULL, checked_out_at TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notices(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  category TEXT NOT NULL, lang TEXT NOT NULL, group_id INTEGER,
  title TEXT NOT NULL, body TEXT NOT NULL,
  posted_at TEXT NOT NULL, author TEXT,
  machine_translated INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS notices_group ON notices(group_id);
CREATE TABLE IF NOT EXISTS chat_logs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session TEXT, lang TEXT, role TEXT, content TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS manual_chunks(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc TEXT NOT NULL, section TEXT, text TEXT NOT NULL, embedding BLOB,
  model TEXT
);
CREATE TABLE IF NOT EXISTS sync_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  transport TEXT, target TEXT, payload_kind TEXT, status TEXT, detail TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
"""


def connect() -> sqlite3.Connection:
    config.db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.db_path, timeout=10, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


@contextmanager
def conn() -> Iterator[sqlite3.Connection]:
    con = connect()
    try:
        yield con
        con.commit()
    finally:
        con.close()


# 後から足した列（既存 DB には ALTER TABLE で足す）: (テーブル, 列, 型)
_ADDED_COLUMNS = [
    ("chat_logs", "kind", "TEXT"),        # 避難者の発言の種類（voices.KINDS）
    ("chat_logs", "topic", "TEXT"),       # 話題（voices.TOPICS）
    ("chat_logs", "status", "TEXT"),      # open / done（運営者が対応済みにする）
    ("chat_logs", "outcome", "TEXT"),     # 回答の結果（voices.OUTCOMES）
    ("chat_logs", "handled_at", "TEXT"),
    ("chat_logs", "evacuee_id", "INTEGER"),          # 自己登録した人からの発言なら受付番号
    ("chat_logs", "classify_how", "TEXT"),  # 分類の決め方 rule / embed / rule+embed（NULL は埋め込みでまだ見ていない）
    ("evacuees", "needs", "TEXT NOT NULL DEFAULT '[]'"),
    ("evacuees", "members", "TEXT NOT NULL DEFAULT '[]'"),  # 同行者（家族）。checkin.normalize の members  # 運営に伝えたいこと（standard_form.NEEDS）
]

# 後から足したテーブル
_ADDED_SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  evacuee_id INTEGER NOT NULL,
  note TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS contacts_evacuee ON contacts(evacuee_id, id);
-- 案件（いまは避難者の「避難所に伝える」だけ。cases.py）
CREATE TABLE IF NOT EXISTS cases(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  evacuee_id INTEGER NOT NULL,
  source TEXT NOT NULL DEFAULT 'message',
  category TEXT NOT NULL,
  urgent INTEGER NOT NULL DEFAULT 0,
  text TEXT,
  lang TEXT,
  level INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  handled_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS cases_evacuee ON cases(evacuee_id, id);
CREATE INDEX IF NOT EXISTS cases_status ON cases(status, id);
"""


def init_db() -> None:
    with conn() as c:
        c.executescript(SCHEMA)
        c.executescript(_ADDED_SCHEMA)
        for table, col, typ in _ADDED_COLUMNS:
            cols = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
            if col not in cols:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
        c.execute("CREATE INDEX IF NOT EXISTS chat_logs_role ON chat_logs(role, id)")
        c.execute("CREATE INDEX IF NOT EXISTS chat_logs_evacuee ON chat_logs(evacuee_id, id)")


# 白紙に戻す対象（名簿・対応メモ・案件・チャット記録・送信ログ）。お知らせ・設定・マニュアルは残す
RESET_TABLES = ("evacuees", "contacts", "cases", "chat_logs", "sync_log")


def reset_records(tables: tuple[str, ...] = RESET_TABLES) -> dict[str, int]:
    """名簿と履歴を消して受付番号を 1 から振り直す。消した件数をテーブルごとに返す。"""
    counts = {}
    with conn() as c:
        for tbl in tables:
            counts[tbl] = c.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
            c.execute(f"DELETE FROM {tbl}")
        c.execute("DELETE FROM sqlite_sequence WHERE name IN (%s)" % ",".join("?" * len(tables)), tables)
    return counts


def query(sql: str, params: tuple | list = ()) -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def query_one(sql: str, params: tuple | list = ()) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: tuple | list = ()) -> int:
    """実行して lastrowid を返す。"""
    with conn() as c:
        cur = c.execute(sql, params)
        return cur.lastrowid or 0


# --- 設定値（画面から変えるもの） ----------------------------------------
# キー → config の属性名。DB に無ければ config（.env）の値を使う
_PROFILE_KEYS = {
    "name": "name",
    "address": "address",
    "host_ip": "host_ip",
    "ssid": "ssid",
    "wifi_password": "wifi_password",
    "sync_transport": "sync_transport",
    "sync_url": "sync_url",
    "area_towns": "area_towns",   # 想定地区の町名（カンマ区切り。stats.breakdown の area）
}


def get_setting(key: str, default: Any = None) -> Any:
    row = query_one("SELECT value FROM settings WHERE key=?", (key,))
    if row is not None and row["value"] is not None:
        return row["value"]
    if key in _PROFILE_KEYS:
        return getattr(config, _PROFILE_KEYS[key])
    return default


def set_setting(key: str, value: Any) -> None:
    execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, None if value is None else str(value)),
    )


def profile() -> dict:
    """避難所の基本情報（画面・AI・同期で共通に使う）。"""
    p = {k: get_setting(k) for k in _PROFILE_KEYS}
    p["entry_url"] = f"http://{p['host_ip']}:{config.port}/"
    return p
