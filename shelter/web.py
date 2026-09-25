"""画面まわりの小物: 運営者 PIN 認証・言語・Markdown の簡易変換・SSE。"""
from __future__ import annotations

import asyncio
import html
import json
import re
from typing import AsyncIterator, Callable

from fastapi import Request
from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

from . import llm
from .config import config

# --- 運営者の認証（共通 PIN → 署名付き Cookie） ---------------------------
STAFF_COOKIE = "shelter_staff"
_signer = TimestampSigner(config.secret_key, salt="shelter-staff")


class NeedLogin(Exception):
    pass


def make_staff_token() -> str:
    return _signer.sign(b"staff").decode()


def is_staff(request: Request) -> bool:
    tok = request.cookies.get(STAFF_COOKIE)
    if not tok:
        return False
    try:
        _signer.unsign(tok, max_age=config.session_hours * 3600)
        return True
    except (BadSignature, SignatureExpired):
        return False


def require_staff(request: Request) -> None:
    if not is_staff(request):
        raise NeedLogin()


# --- 避難者（自己登録した端末 → 署名付き Cookie に受付番号） ------------------
EVACUEE_COOKIE = "shelter_evacuee"
_ev_signer = TimestampSigner(config.secret_key, salt="shelter-evacuee")
EVACUEE_COOKIE_DAYS = 30


def make_evacuee_token(evacuee_id: int) -> str:
    return _ev_signer.sign(str(int(evacuee_id)).encode()).decode()


def evacuee_id_of(request: Request) -> int | None:
    tok = request.cookies.get(EVACUEE_COOKIE)
    if not tok:
        return None
    try:
        return int(_ev_signer.unsign(tok, max_age=EVACUEE_COOKIE_DAYS * 86400).decode())
    except (BadSignature, SignatureExpired, ValueError):
        return None


# --- 言語 -----------------------------------------------------------------
LANG_COOKIE = "shelter_lang"


def get_lang(request: Request) -> str:
    q = request.query_params.get("lang")
    if q in ("ja", "en"):
        return q
    c = request.cookies.get(LANG_COOKIE)
    if c in ("ja", "en"):
        return c
    accept = request.headers.get("accept-language", "")
    return "ja" if (not accept or accept.lower().startswith("ja")) else "en"


T = {
    "ja": {
        "notices": "お知らせ", "chat": "相談する", "info": "避難所の案内", "home": "トップ",
        "reflected": "{t} のお知らせまで反映", "no_notices": "まだお知らせはありません",
        "latest": "最新のお知らせ", "all_notices": "お知らせをすべて見る",
        "chat_disclaimer": "AIの回答は参考です。最終判断は受付（運営本部）へ。",
        "chat_placeholder": "質問を入力（例: 次の配給は？）", "send": "送る",
        "examples": "質問の例", "sources": "根拠", "notice_at": "{t} のお知らせ",
        "waiting": "順番待ち: 前に {n} 人", "thinking": "考えています…", "writing": "答えを書いています…",
        "error": "うまく答えられませんでした。受付で確認してください。",
        "offline_note": "この Wi-Fi はインターネットにつながっていません。避難所の中の情報だけが見られます。",
        "switch": "English", "reset": "会話を消す",
        "posted": "掲載", "machine": "",
        "ai_local": "この PC の AI", "ai_cloud": "クラウド AI (Gemini)",
        "error_cloud": "クラウド AI に接続できません。右上のスイッチで『この PC の AI』に戻してください。",
        "register": "受付をする（登録）", "register_sub": "自分のスマホで登録。受付に並ばなくて大丈夫です",
        "me": "受付番号とバーコード", "me_sub": "職員に見せるとき・登録内容を直すとき",
        "receipt_no": "受付番号", "registered": "受付できました",
        "bar_hint": "職員に声をかけるときは、このバーコードを見せてください。読み取ると、あなたの登録内容に対応の記録がひも付きます。",
        "edit_reg": "登録内容を直す", "saved": "登録内容を更新しました",
        "reg_intro": "あなたの情報を登録すると、受付に並ばずに済み、必要な支援が運営に伝わります。個人情報はこの避難所の PC の中だけに置かれ、外には送られません。",
        "name": "氏名", "kana": "ふりがな", "addr": "住所（町名まででも可）", "sex": "性別", "age": "年齢",
        "hh": "一緒にいる人数（本人を含む）", "pet": "一緒のペットの数", "care": "あてはまるもの（本人の申し出）",
        "alg": "アレルギー", "needs": "避難所の運営に伝えたいこと（あてはまるものすべて）",
        "note": "ほかに伝えたいこと（自由記述）", "note_ph": "例: 透析は火・木・土。かかりつけは〇〇病院",
        "submit_reg": "この内容で受付する", "required": "必須",
        "my_needs": "運営に伝えたこと", "none": "なし", "people": "人",
        "chat_as": "受付番号 {n} として相談中（運営が内容を見て対応します）",
        "already": "この端末はすでに受付済みです（受付番号 {n}）。",
        "next": "次へ", "done_hint": "この番号とバーコードはホーム画面の最上部にいつでも出ます。職員に声をかけるときに見せてください。",
        "members": "一緒に避難した家族・同行者", "members_hint": "子ども・高齢の家族など、一緒に来た人を全員登録してください。名前と年齢だけでも大丈夫です。",
        "add_member": "＋ 同行者を追加", "remove_member": "この人を消す", "member": "同行者", "rel": "続柄",
        "member_care": "あてはまるもの・アレルギー（あれば）", "total_people": "合計 {n} 人（本人を含む）",
        "skip_reg": "あとで登録する（先にお知らせ・相談を見る）",
        "not_registered": "まだ受付していない方へ", "not_registered_sub": "家族全員の分をまとめて登録できます。受付に並ばなくて大丈夫です",
        "tell": "避難所に伝える", "tell_sub": "体調・薬・物資・困りごとを運営に伝えます（返事はお知らせか対面で）",
        "tell_what": "用件（1 つ選ぶ）", "tell_urgent": "急ぐ", "tell_urgent_sub": "すぐに対応してほしいとき",
        "tell_text": "くわしく（任意・200 字まで）", "tell_ph": "例: 血圧の薬があと 1 日分しかない",
        "tell_send": "送る", "tell_need_cat": "用件を 1 つ選んでください",
        "tell_sent": "届きました。返事はお知らせか対面で伝えます。",
        "tell_list": "伝えたこと", "tell_none": "まだありません",
        "tell_as": "受付番号 {n} として送ります（運営が内容を見て対応します）",
    },
    "en": {
        "notices": "Notices", "chat": "Ask", "info": "Shelter guide", "home": "Home",
        "reflected": "Notices up to {t}", "no_notices": "No notices yet",
        "latest": "Latest notices", "all_notices": "See all notices",
        "chat_disclaimer": "AI answers are for reference only. Final decisions: ask the reception.",
        "chat_placeholder": "Type a question (e.g. next food?)", "send": "Send",
        "examples": "Examples", "sources": "Sources", "notice_at": "Notice at {t}",
        "waiting": "Waiting: {n} ahead of you", "thinking": "Thinking…", "writing": "Writing the answer…",
        "error": "Could not answer. Please ask the reception.",
        "offline_note": "This Wi-Fi is not connected to the internet. Only information of this shelter is available.",
        "switch": "日本語", "reset": "Clear chat",
        "posted": "Posted", "machine": "(machine translation)",
        "ai_local": "On this PC", "ai_cloud": "Cloud AI (Gemini)",
        "error_cloud": "Cannot reach the cloud AI. Switch back to \"On this PC\" with the toggle at the top right.",
        "register": "Check in (register)", "register_sub": "Register on your own phone. No need to wait in line",
        "me": "My number & barcode", "me_sub": "Show it to staff, or edit your registration",
        "receipt_no": "Reception no.", "registered": "You are checked in",
        "bar_hint": "When you talk to staff, show this barcode. Scanning it links the staff's notes to your registration.",
        "edit_reg": "Edit my registration", "saved": "Your registration has been updated",
        "reg_intro": "Registering tells the shelter staff what you need, so you do not have to wait in line. Your data stays on this shelter's PC and is never sent outside.",
        "name": "Name", "kana": "Name (kana, optional)", "addr": "Address (town name is enough)", "sex": "Sex", "age": "Age",
        "hh": "People with you (including yourself)", "pet": "Pets with you", "care": "Which apply to you (self-declared)",
        "alg": "Allergies", "needs": "What the shelter staff should know (check all that apply)",
        "note": "Anything else (free text)", "note_ph": "e.g. Dialysis on Tue/Thu/Sat at XX Hospital",
        "submit_reg": "Check in with this", "required": "required",
        "my_needs": "What you told the staff", "none": "None", "people": "people",
        "chat_as": "Chatting as reception no. {n} (staff can see and respond)",
        "already": "This phone is already checked in (reception no. {n}).",
        "next": "Next", "done_hint": "This number and barcode always appear at the top of the home screen. Show it when you talk to staff.",
        "members": "Family / people with you", "members_hint": "Please add everyone who came with you, such as children and older family members. Name and age are enough.",
        "add_member": "+ Add a person", "remove_member": "Remove this person", "member": "Person with you", "rel": "Relationship",
        "member_care": "Which apply / allergies (if any)", "total_people": "Total: {n} (including you)",
        "skip_reg": "Register later (see notices and ask first)",
        "not_registered": "Not checked in yet?", "not_registered_sub": "You can register your whole family at once. No need to wait in line",
        "tell": "Tell the shelter staff", "tell_sub": "Health, medicine, supplies, problems. Staff reply via notices or in person",
        "tell_what": "What is it about? (choose one)", "tell_urgent": "Urgent", "tell_urgent_sub": "Check if you need help right away",
        "tell_text": "Details (optional, up to 200 characters)", "tell_ph": "e.g. I have only 1 day of blood pressure medicine left",
        "tell_send": "Send", "tell_need_cat": "Please choose one topic",
        "tell_sent": "Received. Staff will reply via notices or in person.",
        "tell_list": "What you told the staff", "tell_none": "Nothing yet",
        "tell_as": "Sending as reception no. {n} (staff will see it and respond)",
    },
}


def t(lang: str, key: str, **kw) -> str:
    s = T.get(lang, T["ja"]).get(key) or T["ja"].get(key, key)
    return s.format(**kw) if kw else s


# --- Markdown（避難所の案内用の最小限） --------------------------------------
def _inline(s: str) -> str:
    s = html.escape(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"`(.+?)`", r"<code>\1</code>", s)
    return s


def md_to_html(md: str) -> str:
    out: list[str] = []
    in_list = False
    table: list[list[str]] = []

    def flush_table():
        nonlocal table
        if table:
            head, *rest = table
            rows = [r for r in rest if not all(re.fullmatch(r":?-{2,}:?", c.strip()) for c in r)]
            out.append("<table><thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in head)
                       + "</tr></thead><tbody>"
                       + "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>"
                                 for r in rows) + "</tbody></table>")
            table = []

    for raw in md.splitlines():
        line = raw.rstrip()
        if line.startswith("|"):
            table.append([c.strip() for c in line.strip("|").split("|")])
            continue
        flush_table()
        m = re.match(r"^(#{1,4})\s+(.*)", line)
        li = re.match(r"^\s*[-*]\s+(.*)", line) or re.match(r"^\s*\d+\.\s+(.*)", line)
        if in_list and not li:
            out.append("</ul>")
            in_list = False
        if m:
            lv = min(len(m.group(1)) + 1, 4)
            out.append(f"<h{lv}>{_inline(m.group(2))}</h{lv}>")
        elif li:
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline(li.group(1))}</li>")
        elif line.strip():
            out.append(f"<p>{_inline(line)}</p>")
    flush_table()
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


# --- SSE ------------------------------------------------------------------
def sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


async def sse_llm(
    messages: list[dict],
    max_tokens: int,
    done_extra: Callable[[str], dict] | dict | None = None,
    on_complete: Callable[[str], None] | None = None,
    error_text: str = "AI の応答に失敗しました",
    finalize: Callable[[str], str] | None = None,
    backend: str = "local",
    plan: llm.Plan | None = None,
) -> AsyncIterator[str]:
    """LLM の生成を SSE に流す。順番待ちの間は {"queue": n} を送る（local のときだけ）。

    finalize を渡すと少しずつは流さず、生成し終えた全文を finalize で整えてから1回で送る。
    plan を渡すと「考えてから生成」になり、段階が変わるたびに {"phase": "think"|"write"} を送る。
    """
    q: asyncio.Queue = asyncio.Queue()
    meta: dict = {}

    async def produce():
        try:
            async for piece in llm.stream(messages, max_tokens, meta=meta,
                                          backend=backend, plan=plan):
                await q.put(("d", piece))
            await q.put(("end", None))
        except Exception as e:  # noqa: BLE001
            await q.put(("err", f"{type(e).__name__}: {e}"))

    task = asyncio.create_task(produce())
    parts: list[str] = []
    last_pos = -1
    last_phase = None
    try:
        while True:
            if meta.get("phase") != last_phase:
                last_phase = meta["phase"]
                yield sse({"phase": last_phase})
            try:
                kind, val = await asyncio.wait_for(q.get(), timeout=0.7)
            except asyncio.TimeoutError:
                if "ticket" in meta and not parts:
                    pos = llm.position(meta["ticket"])
                    if pos != last_pos:
                        last_pos = pos
                        yield sse({"queue": pos})
                else:
                    yield ": keep-alive\n\n"
                continue
            if kind == "d":
                parts.append(val)
                if not finalize:
                    yield sse({"delta": val})
            elif kind == "err":
                yield sse({"error": error_text, "detail": val})
                break
            else:
                break
        full = "".join(parts)
        if finalize and full:
            full = finalize(full)
            yield sse({"delta": full})
        extra = done_extra(full) if callable(done_extra) else (done_extra or {})
        if on_complete and full:
            on_complete(full)
        yield sse({"done": True, **extra})
    finally:
        if not task.done():
            task.cancel()


async def sse_text(text: str, extra: dict | None = None) -> AsyncIterator[str]:
    """AI を通さない決まった文を SSE の形で返す。"""
    yield sse({"delta": text})
    yield sse({"done": True, **(extra or {})})
