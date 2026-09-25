"""避難所AI の FastAPI アプリ（避難者ページ・運営者画面・受付・API を1プロセスで出す）。

    python -m uvicorn shelter.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import unicodedata
from contextlib import asynccontextmanager
from urllib.parse import quote, urlencode

import qrcode
from fastapi import Depends, FastAPI, Form, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (HTMLResponse, JSONResponse, RedirectResponse, Response,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import (ai_evacuee, ai_staff, barcode, captive, cases, checkin, db, evidence, gemini, llm,
               notices, priority, rag, stats, sync, voices)
from .config import PKG_DIR, config, now, now_iso
from .schema.standard_form import REL, REL_EN, ALLERGY, CARE, CARE_EN, LANGS, NEEDS, NEEDS_EN, SEX, SEX_EN, SOURCE
from .web import (EVACUEE_COOKIE, EVACUEE_COOKIE_DAYS, LANG_COOKIE, SSE_HEADERS, STAFF_COOKIE,
                  NeedLogin, evacuee_id_of, get_lang, is_staff, make_evacuee_token,
                  make_staff_token, md_to_html, require_staff, sse_llm, sse_text, t)

log = logging.getLogger("shelter")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    n = voices.backfill()
    if n:
        log.info("避難者の声: 以前の発言 %d 件に種類・話題を付けました", n)
    p = db.profile()
    captive.start(p["host_ip"], p["entry_url"])  # Wi-Fi に入るだけでページが開くように
    tasks = []
    if config.warmup:
        async def _boot():
            await llm.warmup()
            try:
                await ai_evacuee.prime_cache()
                log.info("evacuee prompt cache primed (ja/en)")
            except Exception as e:  # noqa: BLE001
                log.warning("prime_cache failed: %s", e)
            try:  # 埋め込みでまだ見ていない発言の分類を見直す（言葉の表で決まらなかったものだけ変わる）
                n_ai = await voices.backfill_ai()
                if n_ai:
                    log.info("避難者の声: %d 件の分類を埋め込みで見直しました", n_ai)
            except Exception as e:  # noqa: BLE001
                log.warning("voices.backfill_ai failed: %s", e)
            if os.getenv("SHELTER_AUTO_INGEST", "1") != "0" and rag.ingest_state()["chunks"] == 0:
                log.info("manual_chunks が空なのでマニュアルを取り込みます（数分）")
                await rag.ingest()
        tasks.append(asyncio.create_task(_boot()))
    yield
    captive.stop()
    for tk in tasks:
        tk.cancel()


app = FastAPI(title="避難所AI", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory=PKG_DIR / "static"), name="static")
templates = Jinja2Templates(directory=PKG_DIR / "templates")
templates.env.globals.update(CARE=CARE, CARE_EN=CARE_EN, ALLERGY=ALLERGY, SEX=SEX, SEX_EN=SEX_EN,
                             LANGS=LANGS, SOURCE=SOURCE, NEEDS=NEEDS, NEEDS_EN=NEEDS_EN,
                             CATEGORIES=notices.CATEGORIES, hhmm=notices.hhmm,
                             KINDS=voices.KINDS, TOPIC_LABEL=voices.TOPIC_LABEL,
                             TOPIC_CATEGORY=voices.TOPIC_CATEGORY, REL=REL, REL_EN=REL_EN)


def err(code: int, message: str, key: str = "") -> JSONResponse:
    return JSONResponse({"error": {"code": key or str(code), "message": message}}, status_code=code)


def _wants_json(request: Request) -> bool:
    p = request.url.path
    return p.startswith("/api/") or p.startswith("/staff/api/") or (
        request.method == "POST" and "application/json" in request.headers.get("content-type", ""))


@app.exception_handler(NeedLogin)
async def _need_login(request: Request, exc: NeedLogin):
    if _wants_json(request):
        return err(401, "運営者の PIN でログインしてください", "unauthorized")
    return RedirectResponse(f"/staff/login?next={quote(str(request.url.path))}", status_code=303)


@app.exception_handler(RequestValidationError)
async def _bad_request(request: Request, exc: RequestValidationError):
    return err(400, f"入力の形式が不正です: {exc.errors()[:3]}", "bad_request")


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, exc: StarletteHTTPException):
    if _wants_json(request):
        return err(exc.status_code, str(exc.detail))
    return HTMLResponse(f"<h1>{exc.status_code}</h1><p>{exc.detail}</p><p><a href='/'>トップへ</a></p>",
                        status_code=exc.status_code)


def current_evacuee(request: Request) -> dict | None:
    """この端末で自己登録した人（Cookie の受付番号 → 在所中の登録）。無ければ None。"""
    eid = evacuee_id_of(request)
    if not eid:
        return None
    ev = checkin.get(eid)
    return ev if ev and ev["active"] else None


def render(request: Request, name: str, **ctx) -> HTMLResponse:
    lang = ctx.pop("lang", None) or get_lang(request)
    base = {"lang": lang, "t": lambda k, **kw: t(lang, k, **kw), "profile": db.profile(),
            "is_staff": is_staff(request), "path": request.url.path,
            "me": ctx.pop("me", None) or current_evacuee(request),
            "barcode_text": barcode.code_text}
    resp = templates.TemplateResponse(request, name, {**base, **ctx})
    if request.query_params.get("lang") in ("ja", "en"):
        resp.set_cookie(LANG_COOKIE, lang, max_age=180 * 86400, samesite="lax")
    return resp


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return {}
    return body if isinstance(body, dict) else {}


# ======================================================================
# 避難者向け（スマホ）
# ======================================================================
REG_SEEN_COOKIE = "shelter_reg_seen"  # この端末で受付画面を一度出した印（2回目からはホームから始まる）


@app.get("/", response_class=HTMLResponse)
def page_index(request: Request):
    lang = get_lang(request)
    # はじめてこの端末で開いたときは受付（自己登録）から始める。職員の PC・登録済み・一度見た端末はホーム
    if not request.cookies.get(REG_SEEN_COOKIE) and not is_staff(request) and not current_evacuee(request):
        q = request.query_params.get("lang")
        return RedirectResponse("/register" + (f"?lang={q}" if q in LANGS else ""), status_code=303)
    items = notices.list_notices(lang, limit=3)
    latest = notices.list_notices(lang, limit=1)
    return render(request, "index.html", lang=lang, notices=items,
                  reflected=latest[0]["hhmm"] if latest else None)


@app.get("/notices", response_class=HTMLResponse)
def page_notices(request: Request):
    lang = get_lang(request)
    return render(request, "notices.html", lang=lang, notices=notices.list_notices(lang))


@app.get("/chat", response_class=HTMLResponse)
def page_chat(request: Request):
    lang = get_lang(request)
    return render(request, "chat.html", lang=lang, suggestions=ai_evacuee.SUGGESTIONS[lang],
                  gemini=llm.status()["gemini"])


@app.get("/info", response_class=HTMLResponse)
def page_info(request: Request):
    lang = get_lang(request)
    return render(request, "info.html", lang=lang,
                  body=md_to_html(ai_evacuee.load_doc("shelter_info", lang)))


# --- 自己登録（避難者が自分のスマホで受付する）と受付番号のバーコード ------------
def _form_members(form) -> list[dict]:
    """フォームの同行者欄（m{番号}_name など）→ members。番号は追加・削除で飛ぶことがある。"""
    out = []
    for i in range(80):
        if form.get(f"m{i}_name") is None:
            continue
        out.append({"name": form.get(f"m{i}_name"), "kana": form.get(f"m{i}_kana"),
                    "rel": form.get(f"m{i}_rel"), "sex": form.get(f"m{i}_sex") or "X",
                    "age": form.get(f"m{i}_age") or None, "dob": form.get(f"m{i}_dob") or None,
                    "care": form.getlist(f"m{i}_care"), "alg": form.getlist(f"m{i}_alg")})
    return out


def _reg_payload(form, lang: str) -> dict:
    return {
        "dev": form.get("dev") or None,
        "name": form.get("name"), "kana": form.get("kana"), "addr": form.get("addr"),
        "sex": form.get("sex") or "X", "age": form.get("age") or None,
        "hh": form.get("hh") or 1, "pet": form.get("pet") or 0,
        "care": form.getlist("care"), "alg": form.getlist("alg"), "needs": form.getlist("needs"),
        "note": form.get("note"), "lang": lang, "members": _form_members(form),
    }


def _set_evacuee_cookie(resp: Response, evacuee_id: int) -> Response:
    resp.set_cookie(EVACUEE_COOKIE, make_evacuee_token(evacuee_id),
                    max_age=EVACUEE_COOKIE_DAYS * 86400, samesite="lax")
    return resp


@app.get("/register", response_class=HTMLResponse)
def page_register(request: Request):
    lang = get_lang(request)
    me = current_evacuee(request)
    resp = render(request, "register.html", lang=lang, me=me, ev=me, error=None)
    resp.set_cookie(REG_SEEN_COOKIE, "1", max_age=180 * 86400, samesite="lax")
    return resp


@app.post("/register", response_class=HTMLResponse)
async def submit_register(request: Request):
    lang = get_lang(request)
    form = await request.form()
    me = current_evacuee(request)
    try:
        p = checkin.normalize(_reg_payload(form, lang), require_dev=False)
    except checkin.PayloadError as e:
        return render(request, "register.html", lang=lang, me=me, ev=me, error=str(e))
    if me:  # この端末で登録済み → 本人が内容を直す（運営メモには触らない）
        checkin.update(me["id"], p, None, keep_staff_note=True)
        return RedirectResponse("/me?saved=1", status_code=303)
    status, ev = checkin.register(p, "app")
    resp = RedirectResponse("/registered" if status != "duplicate" else "/me?dup=1", status_code=303)
    return _set_evacuee_cookie(resp, ev["id"])


@app.get("/registered", response_class=HTMLResponse)
def page_registered(request: Request):
    """受付完了（大きく表示 → 「次へ」でホーム）。次にサイトを開いたときはホームから始まる。"""
    lang = get_lang(request)
    me = current_evacuee(request)
    if not me:
        return RedirectResponse("/register", status_code=303)
    return render(request, "registered.html", lang=lang, me=me)


@app.get("/me", response_class=HTMLResponse)
def page_me(request: Request):
    lang = get_lang(request)
    me = current_evacuee(request)
    if not me:
        return RedirectResponse("/register", status_code=303)
    g = request.query_params.get("guard")
    return render(request, "me.html", lang=lang, me=me, ev=me,
                  flag=next((k for k in ("new", "saved", "dup") if request.query_params.get(k)), None),
                  sent=bool(request.query_params.get("sent")),
                  guard_msg=ai_evacuee.guard_text(g, lang) if g in ("violence", "emergency") else None,
                  told=cases.list_for(me["id"]), **_tell_labels(lang))


# --- 避難所に伝える（登録済みの端末だけ。cases.py） ---------------------------
def _tell_labels(lang: str) -> dict:
    return {"CATS": cases.CATEGORIES_EN if lang == "en" else cases.CATEGORIES,
            "STATUSES": cases.STATUSES_EN if lang == "en" else cases.STATUSES}


@app.get("/tell", response_class=HTMLResponse)
def page_tell(request: Request):
    lang = get_lang(request)
    me = current_evacuee(request)
    if not me:   # 匿名は受けない。先に受付（自己登録）へ
        return RedirectResponse("/register", status_code=303)
    return render(request, "tell.html", lang=lang, me=me, category="", text="", error=None, **_tell_labels(lang))


@app.post("/tell", response_class=HTMLResponse)
async def submit_tell(request: Request):
    lang = get_lang(request)
    me = current_evacuee(request)
    if not me:
        return RedirectResponse("/register", status_code=303)
    form = await request.form()
    category = str(form.get("category") or "")
    text = str(form.get("text") or "").strip()[:cases.TEXT_MAX]
    urgent = bool(form.get("urgent"))
    if category not in cases.CATEGORIES:
        return render(request, "tell.html", lang=lang, me=me, category=category, text=text,
                      error=t(lang, "tell_need_cat"), **_tell_labels(lang))
    g = ai_evacuee.guard_kind(text) if text else None   # 暴力・急病の言葉は窓口の案内を出し、レベル 3 で立てる
    cases.submit(me["id"], category, urgent, text, lang, guarded=bool(g))   # 同じ用件は再送でまとめる（済みなら再オープン）
    return RedirectResponse("/me?sent=1" + (f"&guard={g}" if g else "") + "#told", status_code=303)


@app.get("/me/barcode.svg")
def me_barcode(request: Request):
    me = current_evacuee(request)
    if not me:
        return Response(status_code=404)
    return Response(barcode.svg(me["id"]), media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


# ======================================================================
# API（アプリ・ブラウザが Wi-Fi 経由で叩く）
# ======================================================================
@app.get("/api/health")
def api_health():
    st = llm.status()
    return {"ok": True, "model": st["model"], "embed_model": st["embed_model"], "warm": st["warm"],
            "queue": st["queue"], "tokens_per_sec": st["tokens_per_sec"],
            "last_error": st["last_error"], "manual_chunks": rag.ingest_state()["chunks"],
            "gemini": st["gemini"], "time": now_iso()}


def _rules(lang: str) -> list[str]:
    text = ai_evacuee.load_doc("shelter_info", lang)
    out, on = [], False
    for ln in text.splitlines():
        if ln.startswith("## "):
            on = ("ルール" in ln) or ("rules" in ln.lower())
            continue
        if on and ln.startswith("- "):
            out.append(ln[2:].strip())
    return out


@app.get("/api/shelter")
def api_shelter(lang: str = "ja"):
    p = db.profile()
    latest = db.query_one("SELECT MAX(posted_at) AS t FROM notices") or {}
    s = stats.summary()
    return {"name": p["name"], "address": p["address"], "lang": list(LANGS),
            "updated_at": latest.get("t"), "rules": _rules(lang),
            "facilities": {"entry_url": p["entry_url"], "ssid": p["ssid"],
                           "evacuees": s["total"], "households": s["households"]}}


@app.get("/api/notices")
def api_notices(lang: str = "ja"):
    return [{"id": n["id"], "lang": n["lang"], "title": n["title"], "body": n["body"],
             "posted_at": n["posted_at"], "category": n["category"]}
            for n in notices.list_notices(lang if lang in LANGS else "ja")]


@app.post("/api/chat")
async def api_chat(request: Request):
    body = await _json_body(request)
    message = (body.get("message") or "").strip()
    if not message:
        return err(400, "message は必須です", "bad_request")
    lang = body.get("lang") if body.get("lang") in LANGS else "ja"
    session = str(body.get("session") or "anon")[:64]
    history = body.get("history") if isinstance(body.get("history"), list) else []
    guard_kind = ai_evacuee.guard_kind(message)
    me = current_evacuee(request)  # 自己登録した端末なら発言に受付番号を付ける（運営画面で本人とひも付く）
    eid = me["id"] if me else None
    log_id = ai_evacuee.log_chat(session, lang, "user", message, guard=guard_kind, evacuee_id=eid)
    if not guard_kind:  # 種類・話題を埋め込みで見直す（裏で。答えは待たせない）
        voices.refine_later(log_id, message, lang)

    if guard_kind:
        fixed = ai_evacuee.guard_text(guard_kind, lang)
        ai_evacuee.log_chat(session, lang, "assistant", fixed, evacuee_id=eid)
        voices.set_outcome(log_id, "guard")
        return StreamingResponse(sse_text(fixed, {"sources": [{"type": "guide"}], "guard": True}),
                                 media_type="text/event-stream", headers=SSE_HEADERS)

    try:
        found = await evidence.retrieve(ai_evacuee.search_query(message, history), lang)
    except Exception as e:  # noqa: BLE001  埋め込みが落ちても受付へ案内する答えは返す
        log.warning("evidence.retrieve failed: %s", e)
        found = {"direct": None, "evidence": []}
    if found["direct"]:  # FAQ とほぼ同じ質問 → 書いてある答えをそのまま返す
        answer = found["direct"]["a"]
        ai_evacuee.log_chat(session, lang, "assistant", answer, evacuee_id=eid)
        voices.set_outcome(log_id, "faq")
        return StreamingResponse(sse_text(answer, {"sources": [{"type": "guide"}], "faq": True}),
                                 media_type="text/event-stream", headers=SSE_HEADERS)

    ev = found["evidence"]
    backend = llm.pick(body.get("backend"))
    msgs = ai_evacuee.build_messages(message, lang, history, ev)
    used = list(ev)  # 考えで「使う」と選んだ根拠（後処理と出典に使う）

    def after_think(thought: str) -> list[dict] | str:
        """1段階目の考えを読み、根拠が無ければ生成せずに決まった文、あれば答えを書かせる messages。"""
        ai_evacuee.log_chat(session, lang, "plan", thought, evacuee_id=eid)
        thought, chosen = ai_evacuee.think(thought, ev, lang)
        if chosen is None:
            used.clear()
            return ai_evacuee.NO_ANSWER["en" if lang == "en" else "ja"]
        used[:] = chosen
        return ai_evacuee.answer_messages(msgs, thought, lang)

    def on_complete(full: str) -> None:
        ai_evacuee.log_chat(session, lang, "assistant", full, evacuee_id=eid)
        # 「その情報はまだありません」で終わった質問は、運営画面「避難者の声」で「答えられず」と数える
        voices.set_outcome(log_id, "none" if full.strip() in ai_evacuee.NO_ANSWER.values() else "ai")

    gen = sse_llm(
        msgs, config.max_tokens_evacuee,
        plan=llm.Plan(config.max_tokens_plan, after_think),
        finalize=lambda full: ai_evacuee.clean_answer(full, lang, used),
        done_extra=lambda full: {"sources": ai_evacuee.evidence_sources(used, full),
                                 "backend": backend},
        on_complete=on_complete,
        error_text=t(lang, "error_cloud" if backend == "gemini" else "error"),
        backend=backend)
    return StreamingResponse(gen, media_type="text/event-stream", headers=SSE_HEADERS)


def _checkin_body(body: dict) -> tuple[str, dict | str | None, str | None]:
    source = body.get("source") or "manual"
    if source not in SOURCE:
        raise checkin.PayloadError("source は qr / manual / app のいずれかです")
    return source, body.get("payload"), (body.get("staff_note") or None)


@app.post("/api/checkin/preview")
async def api_checkin_preview(request: Request):
    """QR を読んだ直後の確認用（登録しない）。payload は QR の文字列そのままでも可。"""
    body = await _json_body(request)
    try:
        source, payload, _ = _checkin_body(body)
        p = checkin.normalize(payload, require_dev=(source == "qr"))
    except checkin.PayloadError as e:
        return err(400, str(e), "bad_payload")
    dup = checkin.find_duplicate(p)
    return {"payload": p, "age": checkin.age_of(p["dob"]),
            "duplicate": dup if dup and dup["active"] else None,
            "returning": dup if dup and not dup["active"] else None}


@app.post("/api/checkin")
async def api_checkin(request: Request):
    body = await _json_body(request)
    try:
        source, payload, staff_note = _checkin_body(body)
        p = checkin.normalize(payload, require_dev=(source in ("qr", "app")))
    except checkin.PayloadError as e:
        return err(400, str(e), "bad_payload")
    status, ev = checkin.register(p, source, staff_note)
    return {"id": ev["id"], "status": status, "evacuee": ev}


@app.post("/api/checkout/{evacuee_id}", dependencies=[Depends(require_staff)])
def api_checkout(evacuee_id: int):
    ev = checkin.checkout(evacuee_id)
    if not ev:
        return err(404, "該当する受付番号がありません", "not_found")
    return {"id": ev["id"], "checked_out_at": ev["checked_out_at"]}


# ======================================================================
# 運営者（PC 本体・PIN）
# ======================================================================
@app.get("/staff/login", response_class=HTMLResponse)
def page_login(request: Request, next: str = "/staff"):
    return render(request, "staff/login.html", lang="ja", next=next, error=None)


@app.post("/staff/login")
def do_login(request: Request, pin: str = Form(""), next: str = Form("/staff")):
    if pin.strip() != config.staff_pin:
        return render(request, "staff/login.html", lang="ja", next=next,
                      error="PIN が違います")
    target = next if next.startswith("/staff") else "/staff"
    resp = RedirectResponse(target, status_code=303)
    resp.set_cookie(STAFF_COOKIE, make_staff_token(), max_age=config.session_hours * 3600,
                    httponly=True, samesite="lax")
    return resp


@app.get("/staff/logout")
def do_logout():
    resp = RedirectResponse("/staff/login", status_code=303)
    resp.delete_cookie(STAFF_COOKIE)
    return resp


def staff_render(request: Request, name: str, **ctx) -> HTMLResponse:
    return render(request, name, lang="ja", **ctx)


@app.get("/staff", response_class=HTMLResponse, dependencies=[Depends(require_staff)])
def page_dashboard(request: Request):
    rows = stats.active_evacuees()
    s = stats.summary(rows)
    health = llm.status()
    prio = priority.items()
    return staff_render(request, "staff/dashboard.html", s=s, supplies=stats.supplies(s),
                        b=stats.breakdown(rows), prio=prio[:priority.TOP_N], prio_counts=priority.counts(prio),
                        LEVEL_LABEL=priority.LEVEL_LABEL,
                        notices=notices.list_notices("ja", limit=5),
                        voices=voices.summary(), CASE_CATS=cases.CATEGORIES, CASE_STATUSES=cases.STATUSES,
                        last_sync=db.get_setting("last_sync_at"), health=health,
                        gemini=health["gemini"], manual=rag.ingest_state())


@app.get("/staff/api/summary", dependencies=[Depends(require_staff)])
def api_summary():
    rows = stats.active_evacuees()
    s = stats.summary(rows)
    prio = priority.items()
    return {"summary": s, "supplies": stats.supplies(s), "health": llm.status(),
            "breakdown": stats.breakdown(rows),
            "priority": {**priority.counts(prio), "items": prio[:priority.TOP_N]}}


# --- 対応の優先順（/staff/cases。旧「対応の優先順」と「避難者の声」を 1 本にした画面） ---
def _to_cases(request: Request, rename: dict | None = None) -> RedirectResponse:
    """旧 URL を /staff/cases へ 303（クエリは引き継ぐ。rename で名前を付け替える）。"""
    q = [((rename or {}).get(k, k), v) for k, v in request.query_params.multi_items()]
    return RedirectResponse("/staff/cases" + (f"?{urlencode(q)}" if q else ""), status_code=303)


@app.get("/staff/priority", dependencies=[Depends(require_staff)])
def page_priority(request: Request):
    return _to_cases(request)


@app.get("/staff/voices", dependencies=[Depends(require_staff)])
def page_voices(request: Request):
    # 発言の一覧の「対応」絞り込みは /staff/cases では vstatus（status は案件の状態に使う）
    return _to_cases(request, {"status": "vstatus"})


@app.get("/staff/cases", response_class=HTMLResponse, dependencies=[Depends(require_staff)])
def page_cases(request: Request, status: str = "", level: int = 0, kind: str = "", topic: str = "",
               vstatus: str = "", lang: str = "", q: str = "", hours: int = 24):
    rows = priority.items()
    level = level if level in priority.LEVEL_LABEL else 0
    status = status if status in ("in_progress", "hold", "done") else ""
    shown = [r for r in rows if not level or r["level"] == level]
    if status in ("in_progress", "hold"):
        shown = [r for r in shown if any(k["status"] == status for k in r["cases"])]
    today = now().date().isoformat()
    done_today = db.query("SELECT k.*, e.name FROM cases k LEFT JOIN evacuees e ON e.id = k.evacuee_id"
                          " WHERE k.status='done' AND substr(k.handled_at, 1, 10)=? ORDER BY k.handled_at DESC",
                          (today,))
    n_status = {r["status"]: r["n"] for r in db.query(
        "SELECT status, COUNT(*) AS n FROM cases WHERE status IN ('in_progress','hold') GROUP BY status")}
    h = hours if hours > 0 else None
    return staff_render(request, "staff/cases.html", rows=shown, counts=priority.counts(rows),
                        level=level, status=status, n_status=n_status, done_today=done_today,
                        LEVEL_LABEL=priority.LEVEL_LABEL, CASE_CATS=cases.CATEGORIES,
                        CASE_STATUSES=cases.STATUSES,
                        s=voices.summary(h), vrows=voices.list_voices(kind, topic, vstatus, lang, q.strip(), h),
                        frequent=voices.frequent(h), kind=kind, topic=topic, vstatus=vstatus,
                        lang_f=lang, q=q, hours=hours, gemini=llm.status()["gemini"],
                        list_open=bool(kind or topic or vstatus or lang or q))


@app.get("/staff/api/priority", dependencies=[Depends(require_staff)])
def api_priority(level: int = 0):
    rows = priority.items()
    return {**priority.counts(rows),
            "items": [r for r in rows if r["level"] == level] if level in priority.LEVEL_LABEL else rows}


@app.get("/staff/handle", response_class=HTMLResponse, dependencies=[Depends(require_staff)])
def page_handle(request: Request, q: str = ""):
    """対応タブ（最小版）: 受付番号を読む（USB リーダー）か、番号・氏名を入れて個人ページへ。"""
    q = q.strip()
    digits = unicodedata.normalize("NFKC", q)
    if digits.isdigit():
        return RedirectResponse(f"/staff/roster/{int(digits)}#contact", status_code=303)
    rows = checkin.search(q, "", "all") if q else []
    recent = db.query(
        "SELECT e.id, e.name, MAX(c.created_at) AS last FROM contacts c JOIN evacuees e ON e.id = c.evacuee_id"
        " GROUP BY e.id ORDER BY last DESC LIMIT 5")
    return staff_render(request, "staff/handle.html", q=q, rows=rows, recent=recent)


@app.get("/staff/checkin", response_class=HTMLResponse, dependencies=[Depends(require_staff)])
def page_checkin(request: Request):
    recent = [checkin.to_view(r) for r in db.query(
        "SELECT * FROM evacuees ORDER BY updated_at DESC LIMIT 8")]
    return staff_render(request, "staff/checkin.html", recent=recent)


@app.get("/staff/roster", response_class=HTMLResponse, dependencies=[Depends(require_staff)])
def page_roster(request: Request, q: str = "", care: str = "", status: str = "active"):
    rows = checkin.search(q, care, status)
    return staff_render(request, "staff/roster.html", rows=rows, q=q, care=care, status=status,
                        chats=checkin.chat_counts())


def _evacuee_page(request: Request, ev: dict, **ctx) -> HTMLResponse:
    return staff_render(request, "staff/evacuee.html", ev=ev, contacts=checkin.contacts(ev["id"]),
                        chats=checkin.chats(ev["id"]), told=cases.list_for(ev["id"]),
                        CASE_CATS=cases.CATEGORIES, CASE_STATUSES=cases.STATUSES,
                        barcode_text=barcode.code_text(ev["id"]),
                        **{"error": None, "saved": False, **ctx})


@app.get("/staff/roster/{evacuee_id}", response_class=HTMLResponse,
         dependencies=[Depends(require_staff)])
def page_evacuee(request: Request, evacuee_id: int):
    ev = checkin.get(evacuee_id)
    if not ev:
        raise StarletteHTTPException(404, "該当する受付番号がありません")
    return _evacuee_page(request, ev)


@app.post("/staff/roster/{evacuee_id}/contact", dependencies=[Depends(require_staff)])
def add_contact(evacuee_id: int, note: str = Form(""), close: str = Form("1"), back: str = Form("")):
    """対面対応のメモ。close=1「記録して済みにする」（未対応の相談・伝言も済み）／close=0「記録だけ」。"""
    if not checkin.get(evacuee_id):
        raise StarletteHTTPException(404, "該当する受付番号がありません")
    try:
        checkin.add_contact(evacuee_id, note, close=close != "0")
    except checkin.PayloadError:
        pass
    if not back.startswith("/staff"):
        back = f"/staff/roster/{evacuee_id}#contact"
    return RedirectResponse(back, status_code=303)


@app.get("/staff/roster/{evacuee_id}/barcode.svg", dependencies=[Depends(require_staff)])
def evacuee_barcode(evacuee_id: int):
    if not checkin.get(evacuee_id):
        return Response(status_code=404)
    return Response(barcode.svg(evacuee_id), media_type="image/svg+xml")


def _form_payload(form) -> dict:
    return {
        "name": form.get("name"), "kana": form.get("kana"), "addr": form.get("addr"),
        "sex": form.get("sex") or "X", "dob": form.get("dob") or None,
        "age": form.get("age") or None,
        "hh": form.get("hh") or 1, "care": form.getlist("care"), "alg": form.getlist("alg"),
        "needs": form.getlist("needs"),
        "note": form.get("note"), "lang": form.get("lang") or "ja", "pet": form.get("pet") or 0,
        "members": _form_members(form),
    }


@app.post("/staff/roster/{evacuee_id}", response_class=HTMLResponse,
          dependencies=[Depends(require_staff)])
async def save_evacuee(request: Request, evacuee_id: int):
    form = await request.form()
    ev = checkin.get(evacuee_id)
    if not ev:
        raise StarletteHTTPException(404, "該当する受付番号がありません")
    try:
        p = checkin.normalize(_form_payload(form), require_dev=False)
    except checkin.PayloadError as e:
        return _evacuee_page(request, ev, error=str(e))
    ev = checkin.update(evacuee_id, p, form.get("staff_note") or None)
    return _evacuee_page(request, ev, saved=True)


@app.post("/staff/roster/{evacuee_id}/checkout", dependencies=[Depends(require_staff)])
def form_checkout(evacuee_id: int):
    checkin.checkout(evacuee_id)
    return RedirectResponse(f"/staff/roster/{evacuee_id}", status_code=303)


# --- お知らせ -------------------------------------------------------------
@app.get("/staff/notices", response_class=HTMLResponse, dependencies=[Depends(require_staff)])
def page_staff_notices(request: Request, edit: int | None = None, msg: str = "",
                       category: str = "", title: str = ""):
    # category / title は「避難者の声」からの下書き（多い質問をそのままお知らせにする）
    return staff_render(request, "staff/notices.html", groups=notices.groups(), edit=edit,
                        msg=msg, now_local=now().strftime("%Y-%m-%dT%H:%M"),
                        prefill_category=category if category in notices.CATEGORIES else "",
                        prefill_title=title[:80])


@app.post("/staff/notices", dependencies=[Depends(require_staff)])
async def form_post_notice(category: str = Form("other"), title: str = Form(...),
                           body: str = Form(...), posted_at: str = Form(""),
                           translate: str = Form("1")):
    ts = None
    if posted_at:
        ts = posted_at[:16] + ":00+09:00" if len(posted_at) == 16 else posted_at
    if category not in notices.CATEGORIES:
        category = "other"
    if translate == "1":
        gid, error = await notices.post_with_translation(category, title, body, ts)
    else:
        gid, error = notices.post(category, title, body, ts), None
    msg = error or "投稿しました。英訳を確認して、必要なら直して保存してください。"
    return RedirectResponse(f"/staff/notices?edit={gid}&msg={quote(msg)}#g{gid}", status_code=303)


@app.post("/staff/notices/{gid}/en", dependencies=[Depends(require_staff)])
def form_save_en(gid: int, title: str = Form(...), body: str = Form(...)):
    notices.save_translation(gid, "en", title, body, machine=False)
    return RedirectResponse(f"/staff/notices?msg={quote('英語版を保存しました')}#g{gid}",
                            status_code=303)


@app.post("/staff/notices/{gid}/translate", dependencies=[Depends(require_staff)])
async def form_retranslate(gid: int):
    ja = db.query_one("SELECT * FROM notices WHERE id=?", (gid,))
    if ja:
        try:
            en_t, en_b = await notices.translate_to_en(ja["title"], ja["body"])
            notices.save_translation(gid, "en", en_t, en_b, machine=True)
            msg = "英訳を作り直しました"
        except Exception as e:  # noqa: BLE001
            msg = f"英訳に失敗しました（{type(e).__name__}）"
    else:
        msg = "お知らせが見つかりません"
    return RedirectResponse(f"/staff/notices?edit={gid}&msg={quote(msg)}#g{gid}", status_code=303)


@app.post("/staff/notices/{gid}/delete", dependencies=[Depends(require_staff)])
def form_delete_notice(gid: int):
    notices.delete_group(gid)
    return RedirectResponse(f"/staff/notices?msg={quote('削除しました')}", status_code=303)


# --- 避難者の声（チャットの要望・不満・質問。画面は /staff/cases に統合） ------------
@app.post("/staff/voices/{log_id}/status", dependencies=[Depends(require_staff)])
def form_voice_status(log_id: int, status: str = Form(...), back: str = Form("/staff/cases")):
    voices.set_status(log_id, status)
    if not back.startswith("/staff/"):
        back = "/staff/cases"
    return RedirectResponse(back, status_code=303)


@app.post("/staff/cases/{case_id}/status", dependencies=[Depends(require_staff)])
def form_case_status(case_id: int, status: str = Form(...), back: str = Form("/staff/cases")):
    try:
        cases.set_status(case_id, status)
    except cases.CaseError as e:
        return err(400, str(e))
    if not back.startswith("/staff"):
        back = "/staff/cases"
    return RedirectResponse(back, status_code=303)


@app.get("/staff/api/cases", dependencies=[Depends(require_staff)])
def api_cases():
    """未対応（受付済・対応中・保留）の「避難所に伝える」一覧。"""
    return {"items": cases.open_cases(), "categories": cases.CATEGORIES, "statuses": cases.STATUSES}


@app.get("/staff/api/voices", dependencies=[Depends(require_staff)])
def api_voices(hours: int = 0):
    h = hours if hours > 0 else None
    return {"summary": voices.summary(h), "frequent": voices.frequent(h),
            "recent": voices.list_voices(hours=h, limit=50)}


# --- 運営者の助手 ---------------------------------------------------------
@app.get("/staff/ai", response_class=HTMLResponse, dependencies=[Depends(require_staff)])
def page_ai(request: Request, tab: str = "manual"):
    s = stats.summary()
    return staff_render(request, "staff/ai.html", tab=tab, care_rows=ai_staff.care_list(),
                        supplies=stats.supplies(s), s=s, manual=rag.ingest_state(),
                        gemini=llm.status()["gemini"])


@app.post("/staff/api/ai/{task}", dependencies=[Depends(require_staff)])
async def api_staff_ai(request: Request, task: str):
    body = await _json_body(request)
    try:
        msgs, sources = await ai_staff.build(task, body)
    except ai_staff.TaskError as e:
        return err(400, str(e), "bad_request")
    except Exception as e:  # noqa: BLE001  埋め込みモデルが無い等
        return err(503, f"AI の準備ができていません: {type(e).__name__}: {e}", "ai_unavailable")
    # 要配慮者（名簿の氏名・メモを含む）は外に出さないため、スイッチに関係なく常にこの PC で処理
    backend = llm.pick(body.get("backend"), force_local=(task == "care"))
    error_text = ("クラウド AI に接続できません。右上のスイッチで『この PC の AI』に戻してください"
                  if backend == "gemini" else "AI の応答に失敗しました")
    return StreamingResponse(
        sse_llm(msgs, config.max_tokens_staff, done_extra={"sources": sources, "backend": backend},
                error_text=error_text, backend=backend),
        media_type="text/event-stream", headers=SSE_HEADERS)


@app.post("/staff/api/gemini-test", dependencies=[Depends(require_staff)])
async def api_gemini_test():
    return await gemini.ping()


# --- エクスポート・同期 ----------------------------------------------------
@app.get("/staff/export/roster.csv", dependencies=[Depends(require_staff)])
def export_roster():
    fn = f"roster_{now():%Y%m%d-%H%M}.csv"
    return Response(stats.roster_csv(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{fn}"'})


@app.get("/staff/export/eei.json", dependencies=[Depends(require_staff)])
def export_eei():
    return JSONResponse(stats.eei())


@app.get("/staff/sync", response_class=HTMLResponse, dependencies=[Depends(require_staff)])
def page_sync(request: Request):
    return staff_render(request, "staff/sync.html", logs=sync.recent_logs(),
                        transports=sync.TRANSPORTS, transport=db.get_setting("sync_transport"),
                        sync_url=db.get_setting("sync_url"), token_set=bool(config.sync_token),
                        last_sync=db.get_setting("last_sync_at"), outbox=str(config.outbox_dir))


@app.post("/staff/sync", dependencies=[Depends(require_staff)])
async def api_sync(request: Request):
    body = await _json_body(request)
    name = body.get("transport")
    if name and name not in sync.TRANSPORTS:
        return err(400, f"送り先 {name} は未対応です", "bad_request")
    return await asyncio.to_thread(sync.run, name)


@app.post("/staff/sync/settings", dependencies=[Depends(require_staff)])
def form_sync_settings(transport: str = Form("file"), sync_url: str = Form("")):
    if transport in sync.TRANSPORTS:
        db.set_setting("sync_transport", transport)
    db.set_setting("sync_url", sync_url.strip())
    return RedirectResponse("/staff/sync", status_code=303)


# --- 設定・入口 QR ---------------------------------------------------------
@app.get("/staff/settings", response_class=HTMLResponse, dependencies=[Depends(require_staff)])
def page_settings(request: Request, msg: str = ""):
    stocks = {k: db.get_setting(f"stock_{k}") or "" for k, *_ in stats.SUPPLY_RULES}
    return staff_render(request, "staff/settings.html", msg=msg, stocks=stocks,
                        rules=stats.SUPPLY_RULES, manual=rag.ingest_state(),
                        files=[f.name for f in rag.manual_files()],
                        gemini=llm.status()["gemini"], captive=captive.status())


@app.post("/staff/settings", dependencies=[Depends(require_staff)])
def form_settings(name: str = Form(...), address: str = Form(""), host_ip: str = Form(...),
                  ssid: str = Form(""), wifi_password: str = Form(""), area_towns: str = Form("")):
    for k, v in {"name": name, "address": address, "host_ip": host_ip, "ssid": ssid,
                 "wifi_password": wifi_password}.items():
        db.set_setting(k, v.strip())
    # 想定地区: 読点・全角カンマもカンマにそろえる。空欄なら既定（.env の SHELTER_AREA_TOWNS）に戻す
    towns = [t.strip() for t in area_towns.replace("、", ",").replace("，", ",").split(",") if t.strip()]
    db.set_setting("area_towns", ",".join(towns) or None)
    p = db.profile()
    if p["host_ip"] != captive.status()["host_ip"]:  # IP を変えたら待ち受けも移す
        captive.start(p["host_ip"], p["entry_url"])
    return RedirectResponse(f"/staff/settings?msg={quote('保存しました')}", status_code=303)


@app.post("/staff/settings/stock", dependencies=[Depends(require_staff)])
async def form_stock(request: Request):
    form = await request.form()
    for k, *_ in stats.SUPPLY_RULES:
        v = (form.get(k) or "").strip()
        db.set_setting(f"stock_{k}", v if v.isdigit() else None)
    return RedirectResponse(f"/staff/settings?msg={quote('備蓄量を保存しました')}#stock",
                            status_code=303)


@app.post("/staff/settings/reset", dependencies=[Depends(require_staff)])
def form_reset(confirm: str = Form("")):
    """名簿・対応メモ・チャット記録・送信ログを白紙に戻す（お知らせ・設定・マニュアルは残す）。"""
    if confirm != "白紙":
        return RedirectResponse(f"/staff/settings?msg={quote('確認の言葉が違います。「白紙」と入力してください')}#reset",
                                status_code=303)
    counts = db.reset_records()
    msg = "白紙に戻しました（名簿 %d・対応メモ %d・チャット %d 件）" % (
        counts["evacuees"], counts["contacts"], counts["chat_logs"])
    log.info(msg)
    return RedirectResponse(f"/staff/settings?msg={quote(msg)}#reset", status_code=303)


@app.post("/staff/api/ingest", dependencies=[Depends(require_staff)])
async def api_ingest():
    if not rag.ingest_state()["running"]:
        asyncio.create_task(rag.ingest())
        await asyncio.sleep(0.1)
    return rag.ingest_state()


@app.get("/staff/api/ingest", dependencies=[Depends(require_staff)])
def api_ingest_state():
    return rag.ingest_state()


def _qr_png(data: str, box: int = 10) -> Response:
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=box, border=3)
    qr.add_data(data)
    qr.make(fit=True)
    buf = io.BytesIO()
    qr.make_image(fill_color="black", back_color="white").save(buf, format="PNG")
    return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})


def wifi_qr_text(ssid: str, password: str) -> str:
    def esc(s: str) -> str:
        return "".join("\\" + c if c in '\\;,:"' else c for c in s)
    return f"WIFI:T:{'WPA' if password else 'nopass'};S:{esc(ssid)};P:{esc(password)};;"


@app.get("/staff/entry-qr.png", dependencies=[Depends(require_staff)])
def entry_qr():
    return _qr_png(db.profile()["entry_url"])


@app.get("/staff/wifi-qr.png", dependencies=[Depends(require_staff)])
def wifi_qr():
    p = db.profile()
    return _qr_png(wifi_qr_text(p["ssid"], p["wifi_password"] or ""))


@app.get("/staff/poster", response_class=HTMLResponse, dependencies=[Depends(require_staff)])
def page_poster(request: Request):
    return staff_render(request, "staff/poster.html", captive=captive.status())


@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)


# JSON のまま見たいとき用（デバッグ）
@app.get("/staff/api/evacuees", dependencies=[Depends(require_staff)])
def api_evacuees(q: str = "", care: str = "", status: str = "active"):
    return checkin.search(q, care, status)
