"""避難者チャットの根拠探し: 案内・FAQ・お知らせを1行ずつの「根拠」にし、bge-m3 で質問に近いものを選ぶ。

- FAQ の質問とほぼ同じなら、AI を通さず FAQ の答えをそのまま返す（速く、書いてあるとおりに答える）
- それ以外は近い根拠を数行だけ AI に渡す（小さいモデルでも外しにくく、入力が短いので速い）
- 近い根拠が無ければ決まった文で受付へ案内する
"""
from __future__ import annotations

import asyncio
import re

import numpy as np

from . import llm, notices
from .config import DATA_DIR

# bge-m3 のコサイン類似度のしきい値（scripts/check_evidence.py で調整）
FAQ_DIRECT = 0.77   # これ以上なら FAQ の答えをそのまま返す
NOTICE_MIN = 0.55   # これ以上のお知らせがあれば、FAQ をそのまま返さず AI に渡す
NOTICE_EVIDENCE_MIN = 0.50  # これ以上のお知らせは、FAQ・案内より前に必ず根拠へ入れる
NOTICE_K = 2
WORD_BOOST = 0.10   # 質問の言葉（トイレ・AED など）がそのまま書いてある行に足す
TOP_K = 4

_FAQ_LINE = re.compile(r"^-\s*Q:\s*(.+?)\s*→\s*A:\s*(.+)$")
# 漢字・カタカナ・英数字の2文字以上のまとまり（ひらがなは助詞が多いので使わない）
_JA_WORD = re.compile(r"[一-鿿々]{2,}|[ァ-ヺー]{2,}|[A-Za-z0-9]{2,}")
_EN_STOP = {"what", "where", "when", "which", "there", "does", "have", "with", "from", "this",
            "that", "time", "your", "they", "want", "need", "about", "here", "tell", "could",
            "would", "should", "shelter", "the", "can", "and", "for", "you", "are", "out", "get",
            "how", "any", "lot", "bring", "some", "much", "many", "will", "there"}


def _words(question: str, lang: str) -> list[str]:
    if lang == "en":
        ws = re.findall(r"[a-z0-9]{3,}", question.lower())
        return [w.rstrip("s") for w in ws if w not in _EN_STOP]
    return _JA_WORD.findall(question)


def _word_hit(words: list[str], text: str) -> bool:
    low = text.lower()
    return any(w.lower() in low for w in words)
_facts: dict[str, dict] = {}          # lang -> {"key": mtimes, "rows": [...], "matrix": ndarray}
_notice_vecs: dict[tuple, np.ndarray] = {}  # (id, posted_at, title, body) -> vec
_lock = asyncio.Lock()


def _norm(v) -> np.ndarray:
    a = np.asarray(v, dtype=np.float32)
    return a / (np.linalg.norm(a, axis=-1, keepdims=True) + 1e-9)


def _doc_path(stem: str, lang: str):
    for lg in (lang, "ja"):
        p = DATA_DIR / f"{stem}.{lg}.md"
        if p.exists():
            return p
    return None


def parse_facts(lang: str) -> list[dict]:
    """faq.<lang>.md の「- Q: … → A: …」と shelter_info.<lang>.md の箇条書きを根拠の行にする。"""
    rows: list[dict] = []
    faq = _doc_path("faq", lang)
    if faq:
        section = ""
        for ln in faq.read_text(encoding="utf-8").splitlines():
            if ln.startswith("## "):
                section = ln[3:].strip()
            elif m := _FAQ_LINE.match(ln.strip()):
                q, a = m.group(1).strip(), m.group(2).strip()
                rows.append({"kind": "faq", "section": section, "q": q, "a": a,
                             "text": f"Q: {q} A: {a}", "embed": q})
    info = _doc_path("shelter_info", lang)
    if info:
        section = ""
        for ln in info.read_text(encoding="utf-8").splitlines():
            s = ln.strip()
            if s.startswith("#"):
                section = s.lstrip("#").strip()
            elif s.startswith("- ") and len(s) > 4:
                text = s[2:].strip()
                rows.append({"kind": "info", "section": section, "text": f"{section}: {text}",
                             "embed": f"{section}: {text}"})
    return rows


async def _facts_for(lang: str) -> dict:
    paths = [p for p in (_doc_path("faq", lang), _doc_path("shelter_info", lang)) if p]
    key = tuple((str(p), p.stat().st_mtime) for p in paths)
    cur = _facts.get(lang)
    if cur and cur["key"] == key:
        return cur
    async with _lock:
        cur = _facts.get(lang)
        if cur and cur["key"] == key:
            return cur
        rows = parse_facts(lang)
        vecs = []
        for i in range(0, len(rows), 16):
            vecs += await llm.embed([r["embed"] for r in rows[i:i + 16]])
        cur = {"key": key, "rows": rows, "matrix": _norm(vecs) if vecs else None}
        _facts[lang] = cur
        return cur


async def _notice_rows(lang: str, limit: int = 10) -> tuple[list[dict], np.ndarray | None]:
    items = notices.list_notices(lang, limit=limit)
    todo = [n for n in items if (n["id"], n["posted_at"], n["title"], n["body"]) not in _notice_vecs]
    if todo:
        vecs = await llm.embed([f"{n['category_label']} {n['title']}: {n['body']}" for n in todo])
        for n, v in zip(todo, vecs):
            _notice_vecs[(n["id"], n["posted_at"], n["title"], n["body"])] = _norm(v)
    rows = []
    for n in items:
        if lang == "en":
            text = f"Notice posted {n['posted_at'][:10]} {n['hhmm']} ({n['category_label']}) {n['title']}: {n['body']}"
        else:
            text = f"お知らせ {n['posted_at'][:10]} {n['hhmm']} 掲示（{n['category_label']}）{n['title']}: {n['body']}"
        rows.append({"kind": "notice", "notice": n, "text": text})
    mat = (np.vstack([_notice_vecs[(n["id"], n["posted_at"], n["title"], n["body"])] for n in items])
           if items else None)
    return rows, mat


async def warm(langs: tuple[str, ...] = ("ja", "en")) -> None:
    for lang in langs:
        await _facts_for(lang)


async def retrieve(question: str, lang: str) -> dict:
    """{"direct": FAQ の行 or None, "evidence": [根拠の行（score 付き・近い順）]}"""
    facts = await _facts_for(lang)
    n_rows, n_mat = await _notice_rows(lang)
    q = _norm((await llm.embed([question]))[0])
    words = _words(question, lang)

    scored: list[dict] = []
    for rows, mat in ((facts["rows"], facts["matrix"]), (n_rows, n_mat)):
        if mat is None:
            continue
        for r, s in zip(rows, mat @ q):
            hit = _word_hit(words, r["text"])
            scored.append({**r, "sim": float(s), "hit": hit,
                           "score": float(s) + (WORD_BOOST if hit else 0.0)})
    scored.sort(key=lambda r: -r["score"])

    # FAQ をそのまま返すかは、言葉の補正なしの似ている度で決める（言い回し違いの同じ質問だけ）
    best_faq = max((r for r in scored if r["kind"] == "faq"), key=lambda r: r["sim"], default=None)
    best_notice = next((r for r in scored if r["kind"] == "notice"), None)
    direct = None
    if (best_faq and best_faq["sim"] >= FAQ_DIRECT
            and not (best_notice and best_notice["score"] >= NOTICE_MIN)):
        direct = best_faq
    # お知らせは一番新しく確かな情報なので、近いものを別枠で先頭に入れる
    # （「お知らせに出します」とだけ書いた FAQ に押し出されないように）
    top_notices = [r for r in scored
                   if r["kind"] == "notice" and r["score"] >= NOTICE_EVIDENCE_MIN][:NOTICE_K]
    rest = [r for r in scored if r["kind"] != "notice"][:TOP_K - len(top_notices)]
    return {"direct": direct, "evidence": top_notices + rest, "words": words}
