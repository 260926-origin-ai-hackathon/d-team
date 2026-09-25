"""避難所AI のスモークテスト（仕様書 §15）。

    python scripts/shelter_smoke.py                  # 一時 DB でアプリを内部起動して試す（Ollama は必要）
    python scripts/shelter_smoke.py --url http://localhost:8000   # 起動中のサーバーを試す（データが増える）
    python scripts/shelter_smoke.py --no-ai          # AI を使う項目を飛ばす
    python scripts/shelter_smoke.py --gemini         # クラウド AI（Gemini）でも chat と マニュアル RAG を1回ずつ
                                                     # （サーバーに GEMINI_API_KEY が無ければ飛ばす）

流れ: health → 手入力受付 → 同じ dev の QR 受付が duplicate → お知らせ投稿（英訳）→ chat ja/en
      （お知らせの時刻が含まれる）→ マニュアル RAG → roster.csv の列順 → eei.json の合計が名簿と一致 → 同期
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import secrets
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

results: list[tuple[bool, str]] = []
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass


def check(ok: bool, label: str, detail: str = "") -> bool:
    results.append((ok, label))
    print(f"[{'OK' if ok else 'NG'}] {label}" + (f"  - {detail}" if detail else ""))
    return ok


def sse_collect(client, url: str, body: dict) -> tuple[str, dict, float]:
    t0 = time.perf_counter()
    first = None
    text, done = "", {}
    with client.stream("POST", url, json=body, timeout=300) as r:
        if r.status_code != 200:
            return f"HTTP {r.status_code} {r.read()[:200]!r}", {}, 0
        for line in r.iter_lines():
            if not line.startswith("data: "):
                continue
            ev = json.loads(line[6:])
            if "delta" in ev:
                first = first or time.perf_counter() - t0
                text += ev["delta"]
            elif ev.get("done"):
                done = ev
            elif ev.get("error"):
                done = ev
    return text, done, first or 0


def run(client, pin: str, use_ai: bool, use_gemini: bool = False) -> None:
    r = client.get("/api/health")
    h = r.json()
    check(r.status_code == 200 and h.get("ok"), "GET /api/health", f"model={h.get('model')} warm={h.get('warm')}")
    # はじめての端末はホームではなく受付（自己登録）から始まる。受付画面を一度見たらホーム
    r = client.get("/", follow_redirects=False)
    check(r.status_code == 303 and r.headers.get("location", "").startswith("/register"), "初回アクセスは受付画面へ")
    r = client.get("/register")
    check(r.status_code == 200 and "shelter_reg_seen" in r.cookies and "data-members" in r.text,
          "受付画面（同行者の入力欄つき）")
    check(client.get("/", follow_redirects=False).status_code == 200, "受付画面を見た端末はホームから")
    check("available" in (h.get("gemini") or {}), "health に gemini の状態",
          f"available={h.get('gemini', {}).get('available')}")
    check(client.get("/api/shelter").json().get("name") is not None, "GET /api/shelter")
    for path in ("/", "/notices", "/chat", "/info", "/?lang=en"):
        check(client.get(path).status_code == 200, f"GET {path}")

    # 運営者ログイン
    check(client.get("/staff", follow_redirects=False).status_code in (302, 303, 307), "PIN 無しの /staff はログインへ")
    r = client.post("/staff/login", data={"pin": pin, "next": "/staff"}, follow_redirects=False)
    check(r.status_code == 303 and "shelter_staff" in r.cookies, "PIN でログイン")
    check(client.get("/staff").status_code == 200, "GET /staff（ダッシュボード）")

    # 受付
    dev = secrets.token_hex(8)
    tag = secrets.token_hex(2)
    manual = {"v": 1, "fmt": "hinanjo-checkin", "name": f"手入力 花子{tag}", "kana": "てにゅうりょく はなこ",
              "addr": "大阪市都島区東野田町1-1", "sex": "F", "dob": "1940-01-01", "hh": 2,
              "care": ["NUR"], "alg": [], "note": "夜間トイレ介助", "lang": "ja", "pet": 0}
    r = client.post("/api/checkin", json={"source": "manual", "payload": manual})
    check(r.status_code == 200 and r.json()["status"] == "created", "手入力で受付", str(r.json().get("id")))
    r = client.post("/api/checkin", json={"source": "manual", "payload": manual})
    check(r.json().get("status") == "duplicate", "同じ人の手入力は duplicate")

    qr = {"v": 1, "fmt": "hinanjo-checkin", "dev": dev, "name": f"QR 太郎{tag}", "addr": "大阪市都島区",
          "sex": "M", "dob": "1958-04-02", "hh": 3, "care": ["ILL", "MED"], "alg": ["egg"],
          "note": "高血圧の薬", "lang": "en", "pet": 1}
    r = client.post("/api/checkin", json={"source": "qr", "payload": json.dumps(qr, ensure_ascii=False)})
    j = r.json()
    check(r.status_code == 200 and j["status"] == "created", "QR（文字列）で受付", str(j.get("id")))
    qr_id = j.get("id")
    r = client.post("/api/checkin", json={"source": "qr", "payload": qr})
    check(r.json().get("status") == "duplicate", "同じ dev の QR は duplicate")
    r = client.post("/api/checkin", json={"source": "qr", "payload": {"v": 1, "fmt": "hinanjo-checkin", "name": "x"}})
    check(r.status_code == 400 and "error" in r.json(), "dev の無い QR は 400")

    # 退所 → 再入所
    r = client.post(f"/api/checkout/{qr_id}")
    check(r.status_code == 200 and r.json().get("checked_out_at"), "退所")
    r = client.post("/api/checkin", json={"source": "qr", "payload": qr})
    check(r.json().get("status") == "updated", "退所後の同じ dev は updated（再入所）")

    # 自己登録（避難者のスマホ）→ 受付完了 → ホーム最上部のバーコード → 相談が本人にひも付く
    self_dev = secrets.token_hex(8)
    r = client.get("/")
    check(r.status_code == 200 and "/register" in r.text, "未登録のホームに「受付をする」")
    r = client.post("/register", data={"dev": self_dev, "name": f"自己登録 次郎{tag}", "addr": "大阪市都島区東野田町4-1",
                                        "sex": "M", "age": "70", "pet": "0", "needs": ["dialysis"],
                                        "note": "火木土に透析",
                                        # 同行者（番号は飛んでよい）
                                        "m0_name": f"自己登録 花子{tag}", "m0_rel": "spouse", "m0_sex": "F",
                                        "m0_age": "68", "m0_care": ["NUR"],
                                        "m3_name": f"自己登録 孫{tag}", "m3_rel": "grandchild", "m3_age": "1",
                                        "m3_alg": ["egg"]}, follow_redirects=False)
    check(r.status_code == 303 and r.headers.get("location") == "/registered" and "shelter_evacuee" in r.cookies,
          "POST /register → 受付完了へ（cookie 付き）")
    r = client.get("/registered")
    check(r.status_code == 200 and "/me/barcode.svg" in r.text and 'href="/"' in r.text, "受付完了画面（バーコード・次へ）")
    r = client.get("/")
    check(r.status_code == 200 and "mybar" in r.text and "/register" not in r.text, "登録後のホーム最上部にバーコード")
    r = client.get("/me/barcode.svg")
    check(r.status_code == 200 and b"<svg" in r.content, "GET /me/barcode.svg")
    self_id = int(client.get("/me").text.split('class="bigno">')[1].split("<")[0])
    row0 = next((x for x in client.get("/staff/api/evacuees").json() if x["id"] == self_id), {})
    ms = row0.get("members") or []
    check(row0.get("hh") == 3 and len(ms) == 2 and ms[0]["rel"] == "spouse" and ms[0]["age"] == 68
          and "NUR" in ms[0]["care"] and ms[1]["age"] == 1 and "ALG" in ms[1]["care"],
          "同行者を全員登録（人数は本人＋2人）", str((row0.get("hh"), [(m["name"], m["age"], m["care"]) for m in ms])))
    s0 = client.get("/staff/api/summary").json()["summary"]
    check(s0["registered_companions"] >= 2 and s0["male"] + s0["female"] + s0["unknown"] == s0["total"],
          "集計に同行者の性別・年齢が入る", f"female={s0['female']} infants={s0['infants']} elderly={s0['elderly']}")
    r = client.post("/register", data={"dev": self_dev, "name": f"自己登録 次郎{tag}", "sex": "M",
                                        "needs": ["dialysis", "medicine"],
                                        "m0_name": f"自己登録 花子{tag}", "m0_rel": "spouse", "m0_sex": "F", "m0_age": "68"},
                                  follow_redirects=False)
    check(r.headers.get("location") == "/me?saved=1", "同じ端末からの再送は内容の更新")
    ev_row = client.get("/staff/api/evacuees").json()
    me_row = next((x for x in ev_row if x["id"] == self_id), None)
    check(me_row is not None and me_row["hh"] == 2 and len(me_row["members"]) == 1 and "MED" in me_row["care"]
          and me_row["needs"] == ["dialysis", "medicine"],
          "名簿に1件・同行者を1人消すと人数2・透析→医療機器の区分も付く", str(me_row and (me_row["hh"], me_row["care"], me_row["needs"])))
    r = client.get("/staff/roster")
    check(r.status_code == 200 and "透析に通う" in r.text, "名簿一覧に「運営に伝えたいこと」")
    text, done, _ = sse_collect(client, "/api/chat", {"session": "smoke-self", "lang": "ja", "message": "急に胸が苦しい"})
    check(done.get("guard") is True, "登録者の相談（ガード）を送る")
    r = client.get(f"/staff/roster/{self_id}")
    check(r.status_code == 200 and "急に胸が苦しい" in r.text and "対面対応のメモ" in r.text, "個人ページに本人の相談が出る")
    r = client.post(f"/staff/roster/{self_id}/contact", data={"note": "保健師に引き継ぎ"}, follow_redirects=False)
    check(r.status_code == 303 and "保健師に引き継ぎ" in client.get(f"/staff/roster/{self_id}").text, "対面対応のメモを残す")
    r = client.get("/staff/voices")
    check(r.status_code == 200 and f"自己登録 次郎{tag}" in r.text, "避難者の声に発言者の名前")
    client.cookies.delete("shelter_evacuee")  # 以降は匿名端末として続ける

    # エクスポート
    r = client.get("/staff/export/roster.csv")
    text = r.content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    from shelter.schema.standard_form import ROSTER_COLUMNS
    check(r.status_code == 200 and rows[0] == ROSTER_COLUMNS, "roster.csv の列が標準様式順", f"{len(rows) - 1} 行")
    rel = ROSTER_COLUMNS.index("続柄(拡張)")
    check(any(x[rel] == "配偶者" and x[3] == f"自己登録 花子{tag}" for x in rows[1:]), "roster.csv に同行者が1人1行で出る")
    active = [x for x in rows[1:] if not x[2] and x[rel] == "本人"]  # 世帯（受付）ごとの代表者の行
    eei = client.get("/staff/export/eei.json").json()
    check(eei["evacuees"]["total"] == sum(int(x[9]) for x in active)
          and eei["households"] == len(active)
          and eei["evacuees"]["male"] + eei["evacuees"]["female"] + eei["evacuees"]["unknown"] == eei["evacuees"]["total"],
          "eei.json の合計が名簿と一致", f"total={eei['evacuees']['total']} households={eei['households']}")

    # 同期（file）
    r = client.post("/staff/sync", json={"transport": "file"})
    sent = r.json().get("sent", [])
    check(r.status_code == 200 and len(sent) == 2 and all(s["ok"] for s in sent), "同期（file）で2ファイル",
          sent[0]["target"] if sent else "")

    # ガード（AI を通さない）
    text, done, _ = sse_collect(client, "/api/chat", {"session": "smoke", "lang": "ja", "message": "夜に知らない人に触られた"})
    check(done.get("guard") and "110" in text, "暴力・性被害の相談は決まった案内")

    # 避難者の声（分類・一覧・対応済み）
    from shelter import voices
    check(voices.classify("毛布がほしい")[0] == "request" and voices.classify("次の配給はいつ？") == ("question", "food")
          and voices.classify("夜うるさくて眠れない")[0] == "trouble" and voices.classify("I feel sick") == ("trouble", "medical"),
          "発言の分類（要望・質問・困りごと）")
    check(voices.classify("熱が出た") == ("trouble", "medical") and voices.classify("子どもが吐いた") == ("trouble", "medical")
          and voices.classify("背中がかゆい") == ("trouble", "medical") and voices.classify("I have a fever") == ("trouble", "medical")
          and voices.classify("息子がみつからない") == ("trouble", "family") and voices.classify("水の配給") == ("question", "food")
          and voices.classify("こんにちは")[0] == "other",
          "発言の分類（症状は困りごと・名詞だけは質問・あいさつはその他）",
          str([voices.classify(t) for t in ("熱が出た", "背中がかゆい", "水の配給", "こんにちは")]))
    j = client.get("/staff/api/voices").json()
    em = [r for r in j["recent"] if r["kind"] == "emergency" and r["content"] == "夜に知らない人に触られた"]
    check(bool(em) and em[0]["outcome"] == "guard" and em[0]["status"] == "open" and j["summary"]["kinds"]["emergency"] >= 1,
          "ガードに当たった発言が「緊急・未対応」で記録される")
    r = client.get("/staff/voices?kind=trouble&status=open&hours=0", follow_redirects=False)
    loc = r.headers.get("location", "")
    check(r.status_code == 303 and loc.startswith("/staff/cases?") and "kind=trouble" in loc and "vstatus=open" in loc
          and client.get("/staff/voices?kind=trouble&hours=0&q=触").status_code == 200,
          "GET /staff/voices → /staff/cases へ 303（クエリを引き継ぐ）", loc)
    if em:
        r = client.post(f"/staff/voices/{em[0]['id']}/status", data={"status": "done"}, follow_redirects=False)
        j = client.get("/staff/api/voices").json()
        row = next((x for x in j["recent"] if x["id"] == em[0]["id"]), {})
        check(r.status_code == 303 and row.get("status") == "done" and row.get("handled_at"), "対応済みにできる")
    check(client.get("/staff/notices?category=food&title=%E9%85%8D%E7%B5%A6").status_code == 200, "お知らせの下書き（避難者の声から）")

    run_priority(client, tag)

    if not use_ai:
        return

    # 発言の分類（埋め込み bge-m3 で近い例文）。TestClient のループとは別に1回だけ回す
    import asyncio

    async def _voices_ai():
        return (await voices.classify_ai("熱が出た"), await voices.classify_ai("Where can I get water?", "en"),
                await voices.classify_full("頭がぼーっとする"), await voices.classify_full("赤ちゃんがずっと泣いてる"),
                await voices.classify_full("こんにちは"))
    a1, a2, f1, f2, f3 = asyncio.run(_voices_ai())
    check(a1[:2] == ("trouble", "medical") and a2[:2] == ("question", "water"),
          "発言の分類（埋め込み）: 熱が出た→困りごと・医療／Where can I get water?→質問・水", f"{a1} {a2}")
    check(f1 == ("trouble", "medical", "embed") and f2[1] == "baby" and f2[2] == "embed" and f3[2] == "rule",
          "言葉の表で決まらない発言は埋め込みで分類（あいさつは言葉の一致のまま）", f"{f1} {f2} {f3}")

    # お知らせ（英訳は AI）
    r = client.post("/staff/notices", data={"category": "food", "title": "夕食の配給",
                                             "body": "15:00 から体育館前で配給します。お皿と箸を持ってきてください。",
                                             "posted_at": time.strftime("%Y-%m-%dT") + "14:00", "translate": "1"},
                    follow_redirects=False)
    check(r.status_code == 303, "お知らせ投稿（英訳つき）")
    en = client.get("/api/notices?lang=en").json()
    check(bool(en) and en[0]["lang"] == "en", "英訳が出る", en[0]["title"] if en else "")

    for lang, q in (("ja", "次の配給はいつ、どこですか？"), ("en", "When is the next food distribution?")):
        text, done, first = sse_collect(client, "/api/chat", {"session": "smoke", "lang": lang, "message": q, "history": []})
        ok = ("15:00" in text or "3:00" in text or "3 pm" in text.lower() or "3pm" in text.lower()) and done.get("done")
        check(ok, f"chat {lang}（お知らせの時刻が含まれる）", f"初回応答 {first:.1f}s: {text[:120]!r} sources={done.get('sources')}")
    j = client.get("/staff/api/voices").json()
    check(any(r["outcome"] in ("ai", "faq") and r["kind"] == "question" for r in j["recent"]),
          "質問に回答の結果（ai / faq）が付く")
    text, done, first = sse_collect(client, "/staff/api/ai/voices", {"hours": 0})
    check(bool(text) and done.get("done") and (done.get("sources") or [{}])[0].get("type") == "voices",
          "AI で避難者の声を整理", f"{first:.1f}s: {text[:100]!r}")

    r = client.get("/api/health").json()
    if r.get("manual_chunks"):
        text, done, first = sse_collect(client, "/staff/api/ai/manual", {"question": "要介護の方の夜間の対応は？"})
        check(bool(text) and bool(done.get("sources")), "マニュアル RAG（出典付き）",
              f"{first:.1f}s: {text[:100]!r} / {[s['title'] + ' ' + s['section'] for s in done.get('sources', [])][:2]}")
    else:
        print("[--] マニュアル未取り込みのため RAG は飛ばしました（scripts/ingest_manual.py）")

    # 要配慮者（名簿）はスイッチに関係なく常にこの PC
    text, done, _ = sse_collect(client, "/staff/api/ai/care", {"backend": "gemini"})
    check(done.get("backend") == "local", "要配慮者の洗い出しは gemini を頼んでも local",
          f"backend={done.get('backend')} {text[:60]!r}")

    if use_gemini:
        run_gemini(client, bool(r.get("manual_chunks")))


def _prio(client) -> dict:
    return client.get("/staff/api/priority").json()


def _row_of(prio: dict, evacuee_id) -> dict | None:
    return next((r for r in prio["items"] if r["id"] == evacuee_id), None)


def _fake_row(addr: str, hh: int, lang: str = "ja", needs: list | None = None) -> dict:
    """stats.breakdown に渡す在所中の受付（必要な列だけ）。"""
    return {"addr": addr, "hh": hh, "lang": lang, "needs": needs or [], "care": [], "members": [], "source": "app"}


def run_priority(client, tag: str) -> None:
    """対応の優先順と内訳（docs/dev/dashboard_stats.md §7）。AI は使わない。"""
    from shelter import stats

    # 1. 透析の自己登録が、擦り傷（病気・けがだけ）の手入力より上
    client.cookies.delete("shelter_evacuee")
    r = client.post("/register", data={"dev": secrets.token_hex(8), "name": f"透析 一郎{tag}", "sex": "M", "age": "60",
                                        "needs": ["dialysis"], "note": "月水金に透析",
                                        "m0_name": f"透析 花子{tag}", "m0_rel": "spouse", "m0_sex": "F",
                                        "m0_age": "62", "m0_care": ["NUR"]}, follow_redirects=False)
    dial_id = int(client.get("/me").text.split('class="bigno">')[1].split("<")[0])
    scrape = {"v": 1, "fmt": "hinanjo-checkin", "name": f"擦り傷 二郎{tag}", "addr": "大阪市都島区網島町1",
              "sex": "M", "dob": "1990-01-01", "hh": 1, "care": ["ILL"], "note": "ひざの擦り傷", "lang": "ja"}
    scrape_id = client.post("/api/checkin", json={"source": "manual", "payload": scrape}).json()["id"]
    p = _prio(client)
    ids = [x["id"] for x in p["items"]]
    check(r.status_code == 303 and dial_id in ids and scrape_id in ids and ids.index(dial_id) < ids.index(scrape_id)
          and _row_of(p, dial_id)["level"] == 3 and _row_of(p, scrape_id)["level"] == 2,
          "優先順: 透析の自己登録が擦り傷の手入力より上",
          f"透析 {ids.index(dial_id) + 1 if dial_id in ids else '-'} 位・擦り傷 {ids.index(scrape_id) + 1 if scrape_id in ids else '-'} 位")

    # 2. 同行者に要介護がある世帯は「同行 …」付きの理由でレベル 2（今日中）以上
    fam = {"v": 1, "fmt": "hinanjo-checkin", "name": f"同行 代表{tag}", "addr": "大阪市都島区東野田町2-2",
           "sex": "F", "dob": "1970-05-05", "hh": 2, "lang": "ja",
           "members": [{"name": f"同行 祖母{tag}", "rel": "parent", "sex": "F", "age": 70, "care": ["NUR"]}]}
    fam_id = client.post("/api/checkin", json={"source": "manual", "payload": fam}).json()["id"]
    p = _prio(client)
    fr, dr = _row_of(p, fam_id), _row_of(p, dial_id)
    who_ok = lambda row: any(x["code"] == "care:NUR" and x["who"].startswith("同行") for x in (row or {}).get("reasons", []))  # noqa: E731
    check(fr is not None and fr["level"] >= 2 and who_ok(fr) and who_ok(dr),
          "優先順: 同行者の要介護が「同行 …」付きでレベル 2 以上", str(fr and [(x["label"], x["who"]) for x in fr["reasons"]]))

    # 3. 想定地区の割合（人数ベース）
    rows = [_fake_row("大阪市都島区東野田町1-1", 2), _fake_row("大阪市都島区 東野田町３－３", 1),
            _fake_row("大阪市都島区網島町6-1", 3), _fake_row("", 1)]
    a = stats.breakdown(rows, towns=["東野田町", "Higashinoda"])["area"]
    check(a["in"] == {"households": 2, "people": 3} and a["out"] == {"households": 1, "people": 3}
          and a["unknown"] == {"households": 1, "people": 1} and a["in_pct"] == 43,
          "内訳: 想定地区の割合（東野田町 2・網島町 1・住所なし 1 → 43%）", str(a))

    # 4. 日本語以外（lang が ja 以外 or 伝えたいことに language。二重に数えない）
    rows = [_fake_row("x", 2, "en"), _fake_row("x", 1, "ja", ["language"]), _fake_row("x", 3, "en", ["language"]),
            _fake_row("x", 4, "ja")]
    nj = stats.breakdown(rows, towns=[])["non_japanese"]
    check(nj == {"households": 3, "people": 6, "by_lang": {"en": 5, "unknown": 1}}, "内訳: 日本語以外の世帯・人数", str(nj))

    # 5. 対面対応のメモで消える → 本人がスマホから直すと再び出る
    client.post(f"/staff/roster/{dial_id}/contact", data={"note": "透析の送迎を手配"})
    gone = _row_of(_prio(client), dial_id) is None
    time.sleep(1.1)  # 時刻は秒単位。メモと同じ秒の更新は「メモより前」と区別できないため
    client.post("/register", data={"name": f"透析 一郎{tag}", "sex": "M", "age": "60",
                                   "needs": ["dialysis", "medicine"]}, follow_redirects=False)
    back = _row_of(_prio(client), dial_id)
    check(gone and back is not None and back["level"] == 3,"優先順: メモで消え、本人が内容を直すと再び出る",
          f"gone={gone} back={back and back['level']}")

    # 6. ガードに掛かった登録者の相談で行がレベル 3 に → メモで相談も done
    client.cookies.delete("shelter_evacuee")
    client.post("/register", data={"dev": secrets.token_hex(8), "name": f"不安 三郎{tag}", "sex": "M", "age": "40",
                                   "needs": ["sleep"]}, follow_redirects=False)
    anx_id = int(client.get("/me").text.split('class="bigno">')[1].split("<")[0])
    before = (_row_of(_prio(client), anx_id) or {}).get("level")
    sse_collect(client, "/api/chat", {"session": f"smoke-anx{tag}", "lang": "ja", "message": "急に胸が苦しい"})
    after = _row_of(_prio(client), anx_id) or {}
    client.post(f"/staff/roster/{anx_id}/contact", data={"note": "救護所へ案内"})
    mine = [x for x in client.get("/staff/api/voices").json()["recent"] if x.get("evacuee_id") == anx_id]
    check(before == 1 and after.get("level") == 3 and any(x["code"] == "chat:emergency" for x in after.get("reasons", []))
          and _row_of(_prio(client), anx_id) is None and mine and all(x["status"] == "done" and x["handled_at"] for x in mine),
          "優先順: 登録者の緊急の相談でレベル 3・メモで相談も対応済み",
          f"before={before} after={after.get('level')} status={[x['status'] for x in mine]}")

    # 7. 匿名端末の緊急の相談は「匿名」の別行
    client.cookies.delete("shelter_evacuee")
    sse_collect(client, "/api/chat", {"session": f"smoke-anon{tag}", "lang": "ja", "message": "人が倒れて意識がない"})
    anon = [x for x in client.get("/staff/api/voices").json()["recent"]
            if x["content"] == "人が倒れて意識がない" and x.get("evacuee_id") is None]
    p = _prio(client)
    row = next((x for x in p["items"] if anon and x["log_id"] == anon[0]["id"]), None)
    check(row is not None and row["id"] is None and row["name"] == "匿名" and row["level"] == 3 and p["anonymous"] >= 1,
          "優先順: 匿名端末の緊急の相談が「匿名」の別行", str(row and (row["name"], row["level"], row["action_url"])))

    # 8. eei.json: 氏名を入れない・レベル別の合計＝未対応の件数
    eei = client.get("/staff/export/eei.json").json()
    pr = eei.get("priority") or {}
    dump = json.dumps(eei, ensure_ascii=False)
    check(pr.get("top") and all("name" not in x and "note" not in x for x in pr["top"])
          and f"透析 一郎{tag}" not in dump and "月水金に透析" not in dump and "急に胸が苦しい" not in dump
          and sum(pr["levels"].values()) == pr["open_total"] and {"area", "non_japanese", "needs"} <= eei.keys(),
          "eei.json に優先順（氏名・備考・本文なし・件数が合う）",
          f"levels={pr.get('levels')} open_total={pr.get('open_total')} in_pct={eei.get('area', {}).get('in_pct')}")

    # 9. 画面
    r = client.get("/staff")
    t = r.text
    check(r.status_code == 200 and "対応の優先順" in t and t.find("対応の優先順", t.find("<main")) < t.find("在所人数")
          and "想定地区" in t and "日本語以外" in t and "支援が要る世帯" in t,
          "GET /staff の先頭に「対応の優先順」と内訳")
    r = client.get("/staff/cases")
    r3 = client.get("/staff/priority?level=3", follow_redirects=False)
    check(r.status_code == 200 and "対応の優先順" in r.text and "質問と多い声" in r.text and "発言の一覧" in r.text
          and f"透析 一郎{tag}" in r.text and 'href="/staff/voices"' not in r.text
          and r3.status_code == 303 and r3.headers.get("location") == "/staff/cases?level=3"
          and client.get("/staff/cases?level=3").status_code == 200 and client.get("/staff/cases?status=done").status_code == 200,
          "GET /staff/cases（優先順・質問と多い声・発言の一覧）・/staff/priority は 303")
    s = client.get("/staff/api/summary").json()
    check("breakdown" in s and len(s["priority"]["items"]) <= 10 and "levels" in s["priority"],
          "GET /staff/api/summary に内訳と優先順")

    run_tell(client, tag)


def run_tell(client, tag: str) -> None:
    """避難所に伝える（登録済みの端末だけ・案件表の最小版。docs/dev/ops_console_plan.md §4）。"""
    # 未登録の端末は /tell で受付へ。トップにも大ボタンが出ない
    client.cookies.delete("shelter_evacuee")
    r = client.get("/tell", follow_redirects=False)
    top = client.get("/?lang=ja").text
    check(r.status_code == 303 and r.headers.get("location", "").startswith("/register")
          and 'href="/tell"' in top and "先に受付（登録）をしてから伝えられます" in top,
          "伝える: 未登録の端末もボタンは出る（受付を先に）・/tell は /register へ 303")

    client.post("/register", data={"dev": secrets.token_hex(8), "name": f"伝える 四郎{tag}", "sex": "M", "age": "50"},
                follow_redirects=False)
    tid = int(client.get("/me").text.split('class="bigno">')[1].split("<")[0])
    check('href="/tell"' in client.get("/").text and client.get("/tell").status_code == 200,
          "伝える: 登録済みの端末はトップに大ボタン・GET /tell")
    body = f"血圧の薬があと1日分しかない{tag}"
    r = client.post("/tell", data={"category": "medical", "urgent": "1", "text": body}, follow_redirects=False)
    me = client.get("/me?sent=1&lang=ja").text   # 前の項目で英語にしているので日本語に戻す
    check(r.status_code == 303 and "/me?sent=1" in r.headers.get("location", "")
          and "伝えたこと" in me and "医療・体調" in me and "受付済" in me and "届きました" in me,
          "伝える: POST /tell → /me に「伝えたこと」と状態", r.headers.get("location", ""))
    row = _row_of(_prio(client), tid) or {}
    rs = [x for x in row.get("reasons", []) if x["code"] == "case:medical"]
    check(row.get("level") == 3 and rs and rs[0]["label"].startswith("伝える:") and row.get("case_ids"),
          "伝える: 優先順にレベル 3 の理由「伝える:」", str(rs and rs[0]["label"]))
    check(_prio(client).get("cases_open", 0) >= 1 and "伝える 未対応" in client.get("/staff").text
          and any(x["evacuee_id"] == tid for x in client.get("/staff/api/cases").json()["items"]),
          "伝える: ダッシュボードの件数チップ・GET /staff/api/cases")

    # ガードに当たる本文は窓口の案内を出し、分類が何でもレベル 3
    r = client.post("/tell", data={"category": "other", "text": "急に胸が苦しい"}, follow_redirects=False)
    loc = r.headers.get("location", "")
    check("guard=emergency" in loc and "119" in client.get(loc.split("#")[0]).text
          and any(x["level"] == 3 and x["category"] == "other" for x in client.get("/staff/api/cases").json()["items"]),
          "伝える: 急病の言葉は窓口の案内・レベル 3", loc)

    # 伝言の本文が /staff/cases（行の展開部）と個人ページに出る
    page = client.get("/staff/cases").text
    check(body in page and f"/staff/roster/{tid}/contact" in page and "記録して済みにする" in page and "記録だけ" in page
          and "保健師に引き継ぎ" in page, "伝える: /staff/cases に伝言の本文・メモ欄・定型文")
    page = client.get(f"/staff/roster/{tid}").text
    check(body in page and "この人の伝言" in page and "/staff/cases/" in page, "伝える: 個人ページに伝言の本文と状態ボタン")

    # 「記録だけ」は案件を開いたまま
    r = client.post(f"/staff/roster/{tid}/contact", data={"note": "薬の名前を確認中", "close": "0", "back": "/staff/cases"},
                    follow_redirects=False)
    mine = [x for x in client.get("/staff/api/cases").json()["items"] if x["evacuee_id"] == tid]
    check(r.status_code == 303 and r.headers.get("location") == "/staff/cases" and len(mine) >= 1
          and all(x["status"] == "open" for x in mine) and "薬の名前を確認中" in client.get(f"/staff/roster/{tid}").text,
          "伝える: 「記録だけ」ではメモが残り伝言は開いたまま", str([x["status"] for x in mine]))

    # 「記録して済みにする」（close=1）: done になり行が消える
    client.post(f"/staff/roster/{tid}/contact", data={"note": "薬を手配", "close": "1"})
    me = client.get("/me").text
    check(_row_of(_prio(client), tid) is None and "対応済" in me and "受付済" not in me,
          "伝える: 対応メモで対応済みになり優先順から消える")

    # 「済み」ボタン（1 件だけ対応済みに）
    client.post("/tell", data={"category": "food", "text": "水が足りない"}, follow_redirects=False)
    cid = next(x["id"] for x in client.get("/staff/api/cases").json()["items"] if x["evacuee_id"] == tid)
    r = client.post(f"/staff/cases/{cid}/status", data={"status": "done"}, follow_redirects=False)
    check(r.status_code == 303 and _row_of(_prio(client), tid) is None, "伝える: 「済み」ボタンで消える")

    # 状態ボタン「対応中」「保留」: 優先順の理由に状態を添え、本人の /me にも出る
    client.post("/tell", data={"category": "baby", "text": "粉ミルクが足りない"}, follow_redirects=False)
    bid = next(x["id"] for x in client.get("/staff/api/cases").json()["items"]
               if x["evacuee_id"] == tid and x["category"] == "baby")
    page = client.get("/staff/priority").text
    has_btns = f"/staff/cases/{bid}/status" in page and 'value="in_progress"' in page and 'value="hold"' in page
    r = client.post(f"/staff/cases/{bid}/status", data={"status": "in_progress"}, follow_redirects=False)
    rs = [x for x in (_row_of(_prio(client), tid) or {}).get("reasons", []) if x["code"] == "case:baby"]
    check(has_btns and r.status_code == 303 and rs and "（対応中）" in rs[0]["label"]
          and "対応中" in client.get("/me?lang=ja").text, "伝える: 状態ボタン「対応中」→ 理由チップと /me に対応中",
          str(rs and rs[0]["label"]))
    client.post(f"/staff/cases/{bid}/status", data={"status": "done"}, follow_redirects=False)

    # 同じ用件の再送: 済みなら新規にせず再オープン（同じ id・受付済に戻る）
    client.post("/tell", data={"category": "food", "text": "水がまた足りない"}, follow_redirects=False)
    foods = [x for x in client.get("/staff/api/cases").json()["items"] if x["evacuee_id"] == tid and x["category"] == "food"]
    check(len(foods) == 1 and foods[0]["id"] == cid and foods[0]["status"] == "open"
          and foods[0]["text"] == "水がまた足りない" and _row_of(_prio(client), tid) is not None,
          "伝える: 同じ用件の再送は済みの案件を再オープン", str(foods))
    client.post(f"/staff/cases/{cid}/status", data={"status": "done"}, follow_redirects=False)

    # eei.json: 分類 × 状態の件数だけ（本文なし）
    eei = client.get("/staff/export/eei.json").json()
    dump = json.dumps(eei, ensure_ascii=False)
    check(eei.get("cases", {}).get("medical", {}).get("done", 0) >= 1 and body not in dump and "水が足りない" not in dump
          and f"伝える 四郎{tag}" not in dump, "伝える: eei.json の cases は件数だけ（本文・氏名なし）",
          str(eei.get("cases", {}).get("medical")))
    client.cookies.delete("shelter_evacuee")

    # 対応タブ（最小版）: 数字（全角・0 埋めも）→ 個人ページ／文字 → 名簿検索／最近対応した人
    r = client.get(f"/staff/handle?q={tid:06d}", follow_redirects=False)
    r2 = client.get("/staff/handle?q=" + quote(f"伝える 四郎{tag}"))
    check(r.status_code == 303 and r.headers.get("location", "").startswith(f"/staff/roster/{tid}")
          and r2.status_code == 200 and f"/staff/roster/{tid}#contact" in r2.text and "最近対応した人" in r2.text
          and "保健師に引き継ぎ" in client.get(f"/staff/roster/{tid}").text,
          "対応タブ: 番号で個人ページへ・氏名で検索・定型文ボタン", r.headers.get("location", ""))


def run_gemini(client, has_manual: bool) -> None:
    g = client.get("/api/health").json().get("gemini") or {}
    if not g.get("available"):
        print("[--] GEMINI_API_KEY が無いので Gemini の項目は飛ばしました")
        return
    r = client.post("/staff/api/gemini-test").json()
    check(r.get("ok"), "Gemini 接続テスト", f"{r.get('model')} {r.get('latency_ms')} ms {r.get('error', '')}")
    text, done, first = sse_collect(client, "/api/chat", {"session": "smoke", "lang": "ja", "backend": "gemini",
                                                          "message": "次の配給はいつ、どこですか？", "history": []})
    check(done.get("backend") == "gemini" and "15:00" in text, "chat ja を Gemini で（お知らせの時刻が含まれる）",
          f"初回応答 {first:.1f}s: {text[:120]!r} {done.get('detail', '')}")
    if has_manual:
        text, done, first = sse_collect(client, "/staff/api/ai/manual",
                                        {"question": "要介護の方の夜間の対応は？", "backend": "gemini"})
        check(done.get("backend") == "gemini" and bool(text) and bool(done.get("sources")),
              "マニュアル RAG を Gemini で（出典付き）", f"{first:.1f}s: {text[:100]!r} {done.get('detail', '')}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", help="起動中のサーバー（省略時は一時 DB で内部起動）")
    ap.add_argument("--pin", default=None)
    ap.add_argument("--no-ai", action="store_true")
    ap.add_argument("--gemini", action="store_true", help="Gemini でも chat とマニュアル RAG を試す")
    args = ap.parse_args()

    if args.url:
        import httpx
        from shelter.config import config
        with httpx.Client(base_url=args.url, timeout=300) as client:
            run(client, args.pin or config.staff_pin, not args.no_ai, args.gemini)
    else:
        tmp = Path(tempfile.mkdtemp(prefix="shelter_smoke_"))
        main_db = ROOT / "shelter" / "data" / "shelter.db"
        os.environ["SHELTER_DB"] = str(tmp / "smoke.db")
        os.environ["SHELTER_OUTBOX"] = str(tmp / "outbox")
        os.environ["SHELTER_AUTO_INGEST"] = "0"
        os.environ["SHELTER_WARMUP"] = "0" if args.no_ai else "1"
        from fastapi.testclient import TestClient

        from shelter import db
        from shelter.config import config
        from shelter.main import app
        db.init_db()
        if main_db.exists():  # 本番 DB の埋め込み済みマニュアルを借りる（RAG を試すため）
            con = sqlite3.connect(os.environ["SHELTER_DB"])
            con.execute("ATTACH DATABASE ? AS src", (str(main_db),))
            try:
                con.execute("INSERT INTO manual_chunks SELECT * FROM src.manual_chunks")
                con.commit()
            except sqlite3.Error:
                pass
            con.close()
        print(f"一時 DB: {tmp}")
        with TestClient(app) as client:
            run(client, args.pin or config.staff_pin, not args.no_ai, args.gemini)

    ng = [label for ok, label in results if not ok]
    print(f"\n{len(results) - len(ng)}/{len(results)} OK" + (f"  NG: {ng}" if ng else ""))
    return 1 if ng else 0


if __name__ == "__main__":
    sys.exit(main())
