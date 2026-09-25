"""運営マニュアルの RAG: チャンク化 → Ollama 埋め込み → SQLite(BLOB) → numpy でコサイン検索。"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

import numpy as np

from . import db, llm
from .config import DATA_DIR, config

log = logging.getLogger("shelter.rag")

MANUAL_DIR = DATA_DIR / "manual"
CHUNK = 500
OVERLAP = 100
TOP_K = 4

# ファイル名（拡張子なし）→ 画面に出す文書名。無いものはファイル名のまま
DOC_TITLES = {
    "osaka_hinanjo_guideline_honpen_R7": "大阪市 避難所開設・運営ガイドライン（本編・R7）",
    "osaka_hinanjo_guideline_shiryo_R7": "大阪市 避難所開設・運営ガイドライン（資料編・R7）",
    "2412hinanjo_guideline": "内閣府 避難所運営等避難生活支援のためのガイドライン（R6.12）",
    "2412kankyokakuho": "内閣府 避難所における良好な生活環境の確保に向けた取組指針（R6.12）",
    "1604hinanjo_toilet_guideline": "内閣府 避難所におけるトイレの確保・管理ガイドライン",
    "osaka_pet_guide": "大阪市 ペット同行避難ガイドライン",
    "osaka_pet_manual": "大阪市 ペットの一時飼育場所 開設運営マニュアル",
    "saitama_seibu_kyumei3": "埼玉西部消防局 普通救命講習テキストⅢ",
    "mhlw_shougaiji_hairyo_R1": "厚労省 避難所等で生活する障害児者への配慮事項",
    "first_aid_essentials.ja": "避難所での応急手当の要点",
}
# 同点に近いときに優先する文書（shelter/data/README.md の優先順）
DOC_BOOST = {
    "osaka_hinanjo_guideline_honpen_R7": 0.02,
    "2412hinanjo_guideline": 0.015,
    "2412kankyokakuho": 0.01,
    "1604hinanjo_toilet_guideline": 0.005,
}

_CJK = r"[　-ヿ㐀-鿿＀-￯]"
_cache: dict = {"matrix": None, "rows": None}
_ingest_state = {"running": False, "done": 0, "total": 0, "message": ""}


def ingest_state() -> dict:
    n = (db.query_one("SELECT COUNT(*) AS n FROM manual_chunks") or {"n": 0})["n"]
    docs = db.query("SELECT doc, COUNT(*) AS n FROM manual_chunks GROUP BY doc ORDER BY doc")
    return {**_ingest_state, "chunks": n, "docs": docs}


def _clean(text: str) -> str:
    """PDF 抽出テキストの整形: 行頭末の空白除去、日本語の途中改行・字間スペースを詰める。"""
    lines = [ln.strip() for ln in text.splitlines()]
    text = "\n".join(ln for ln in lines if ln)
    text = re.sub(rf"(?<={_CJK})[ \t]+(?={_CJK})", "", text)
    text = re.sub(rf"(?<={_CJK})\n(?={_CJK})", "", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def _sections(path: Path) -> list[tuple[str, str]]:
    """(節名, 本文) の列。txt は <<page N>> 単位、md は見出し単位。"""
    raw = path.read_text(encoding="utf-8", errors="ignore")
    out: list[tuple[str, str]] = []
    if path.suffix == ".txt":
        parts = re.split(r"<<page (\d+)>>", raw)
        # parts = [前置き, 番号, 本文, 番号, 本文, ...]
        for i in range(1, len(parts) - 1, 2):
            out.append((f"p.{parts[i]}", _clean(parts[i + 1])))
    else:
        cur, buf = "冒頭", []
        for ln in raw.splitlines():
            m = re.match(r"^#{1,4}\s+(.*)", ln)
            if m:
                if buf:
                    out.append((cur, _clean("\n".join(buf))))
                cur, buf = m.group(1).strip(), []
            else:
                buf.append(ln)
        if buf:
            out.append((cur, _clean("\n".join(buf))))
    return [(s, t) for s, t in out if len(t) >= 30]


def chunk_file(path: Path) -> list[dict]:
    doc = path.stem
    chunks = []
    for section, text in _sections(path):
        start = 0
        while start < len(text):
            piece = text[start:start + CHUNK]
            if len(piece) >= 30:
                chunks.append({"doc": doc, "section": section, "text": piece})
            if start + CHUNK >= len(text):
                break
            start += CHUNK - OVERLAP
    return chunks


def manual_files() -> list[Path]:
    files = sorted(MANUAL_DIR.glob("*.txt")) + sorted(MANUAL_DIR.glob("*.md"))
    return [f for f in files if not f.name.lower().startswith("readme")]


async def ingest(batch: int = 16) -> dict:
    """マニュアルを全部埋め込み直す。PDF は同名 .txt（fetch_public_data.py が抽出済み）を使う。"""
    if _ingest_state["running"]:
        return ingest_state()
    _ingest_state.update(running=True, done=0, total=0, message="チャンク化中")
    try:
        chunks = [c for f in manual_files() for c in chunk_file(f)]
        _ingest_state.update(total=len(chunks), message="埋め込み中")
        rows = []
        for i in range(0, len(chunks), batch):
            part = chunks[i:i + batch]
            vecs = await llm.embed([f"{DOC_TITLES.get(c['doc'], c['doc'])} {c['section']}\n{c['text']}"
                                    for c in part])
            for c, v in zip(part, vecs):
                rows.append((c["doc"], c["section"], c["text"],
                             np.asarray(v, dtype=np.float32).tobytes(), config.embed_model))
            _ingest_state["done"] = min(i + batch, len(chunks))
        with db.conn() as con:
            con.execute("DELETE FROM manual_chunks")
            con.executemany(
                "INSERT INTO manual_chunks(doc, section, text, embedding, model) VALUES(?,?,?,?,?)",
                rows)
        _cache.update(matrix=None, rows=None)
        _ingest_state["message"] = f"完了: {len(rows)} チャンク（{config.embed_model}）"
        log.info("ingest done: %d chunks", len(rows))
    except Exception as e:  # noqa: BLE001
        _ingest_state["message"] = f"失敗: {type(e).__name__}: {e}"
        log.exception("ingest failed")
    finally:
        _ingest_state["running"] = False
    return ingest_state()


def _load() -> tuple[np.ndarray | None, list[dict]]:
    if _cache["matrix"] is None:
        rows = db.query("SELECT id, doc, section, text, embedding FROM manual_chunks"
                        " WHERE embedding IS NOT NULL")
        if not rows:
            return None, []
        mat = np.vstack([np.frombuffer(r.pop("embedding"), dtype=np.float32) for r in rows])
        mat /= np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9
        _cache.update(matrix=mat, rows=rows)
    return _cache["matrix"], _cache["rows"]


async def search(question: str, k: int = TOP_K) -> list[dict]:
    mat, rows = await asyncio.to_thread(_load)
    if mat is None:
        return []
    q = np.asarray((await llm.embed([question]))[0], dtype=np.float32)
    q /= np.linalg.norm(q) + 1e-9
    scores = mat @ q
    scores = scores + np.array([DOC_BOOST.get(r["doc"], 0.0) for r in rows], dtype=np.float32)
    top = np.argsort(-scores)[:k]
    return [
        {**rows[i], "title": DOC_TITLES.get(rows[i]["doc"], rows[i]["doc"]),
         "score": round(float(scores[i]), 3)}
        for i in top
    ]
