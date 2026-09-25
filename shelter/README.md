# 避難所AI（避難所PC側）

仕様: [docs/spec/避難所AI_仕様書_v1.0.md](../docs/spec/避難所AI_仕様書_v1.0.md)

避難所の PC 1 台が、ネットが無くても **受付・名簿・お知らせ・多言語の相談窓口・運営者の助手** を動かす。
避難者は PC が出す Wi-Fi（モバイルホットスポット）に入り、スマホのブラウザで使う。AI も個人情報も PC の中で完結する。

## 起動

```powershell
# 初回だけ（ダウンロード合計 約5.5GB。GPU 無しノートPCで約25分＋マニュアル取り込み約20分）
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
#   Ollama・venv・requirements に加え、qwen3.5:4b（キット用）・qwen3:1.7b（避難所AIのチャット）・bge-m3（埋め込み）を取得
#   .env が無ければ .env.example から作る（SHELTER_CHAT_MODEL=qwen3:1.7b）
.\.venv\Scripts\python.exe scripts\ingest_manual.py              # マニュアルを埋め込む（CPU で約20分。起動時にも空なら自動で実行）
# 管理者 PowerShell で 8000 番を許可（Windows が初回に Python の通信許可を聞いてきて「許可」済みなら不要）
netsh advfirewall firewall add rule name="ShelterAI 8000" dir=in action=allow protocol=TCP localport=8000

# 毎回
# 1) Windows の設定 → モバイルホットスポットを ON（SSID/パスワードは .env の SHELTER_SSID 等と合わせる）
#    ※ ホットスポットは PC の Wi-Fi を使い、共有元のネット接続が無いと ON にできない（2026-09-25 確認）。
#      ネット無しで見せるならトラベルルーターかスマホのテザリングに PC とスマホをつなぎ、SHELTER_HOST_IP をその IP に
#    ※ Windows のモバイルホットスポットは、つないでいる端末が無いと数分で**自動 OFF** になる（省電力）。設定 → モバイルホットスポットの「省電力」を OFF にしておく（2026-09-25・検証中に OFF になり 53/80 番の待ち受けに失敗した）
# 2) 起動（.env のチャット・埋め込みモデルが無ければ取得してから立ち上がる）
powershell -ExecutionPolicy Bypass -File scripts\run_shelter.ps1
#    起動後 1〜2 分は準備中（モデルの読み込み・根拠の埋め込み・前置きの先読み）。デモの 5 分前には起動しておく
```

| 誰が | どこで | URL |
|---|---|---|
| 運営者 | PC 本体のブラウザ | `http://localhost:8000/staff`（PIN は `.env` の `SHELTER_STAFF_PIN`、既定 `1234`） |
| 受付（カメラ） | PC 本体のブラウザ | `http://localhost:8000/staff/checkin`（カメラは localhost でしか使えない） |
| 避難者 | スマホ（ホットスポットに接続） | `http://192.168.137.1:8000/`（入口ポスターの ① Wi-Fi の QR → ② ページの QR） |

入口ポスターは **設定 → 入口ポスターを印刷**。**① Wi-Fi の QR → ② ページの QR の 2 枚**で案内する（v1.5）。
接続時にページを自動で開く機能（**キャプティブポータル**・`shelter/captive.py`）は残してあるが**既定は OFF**。PC の IP の 53 番（DNS・全部 PC の IP と答える）と 80 番（入口ページへ 302）を待ち、Windows ホットスポット（ICS）の DNS 中継より優先される。iPhone で接続確認の横取りまでは届いたが、出方が機種・設定で揃わない（Android は通知だけの機種が多い・モバイルデータが生きていると外れる・プライベート DNS や VPN で素通り）うえ、サインイン画面は Safari と保存領域が別になりうる（自己登録の端末 ID・クッキーが別人扱いになるおそれ・未確認）ため。試すときは `.env` に `SHELTER_CAPTIVE=1` を書き、ファイアウォールで **UDP 53・TCP 80** も許可する。

## 画面

- 避難者: `/` トップ（**はじめての端末は `/register` から始まる**。未登録なら下に「まだ受付していない方へ」。登録済みなら**最上部に受付番号のバーコード**）／
  `/register` 自己登録（氏名・住所・要配慮・**運営に伝えたいこと**〈透析・薬・粉ミルクなど 10 択〉・自由記述・**同行者〈家族〉を全員**。端末 ID で二重登録を防ぐ）→
  `/registered` 受付完了（番号とバーコードを大きく →「次へ」でトップ）／`/me` 受付番号・バーコード・登録内容を直す・**伝えたことと状態**〈受付済／対応中／保留／対応済〉／
  `/tell` **避難所に伝える**（登録済みの端末だけ。用件 8 種・急ぐ・本文 200 字。同じ用件の再送は 1 件にまとめ、対応済みなら受付済に戻す）／
  `/notices`／`/chat`（AI 相談・日英・根拠のお知らせ時刻付き。登録済み端末の発言は受付番号付きで記録）／`/info`
- 運営者: `/staff` ダッシュボード（**先頭に「対応の優先順」の上位 10 行**・内訳〈想定地区の住民 %・日本語以外・支援が要る世帯〉）／
  `/staff/cases` **対応の優先順**（旧「対応の優先順」と「避難者の声」を 1 本化。件数チップ〈未対応のレベル別・対応中・保留・済み（今日）〉→ 人ごとに 1 行〈名簿の伝えたいこと・要配慮〈同行者も〉・75 歳以上、チャットの未対応の相談、伝言〉・3 段階〈至急・今日中・通常〉。AI は使わずルールで並べる。**行を開くと伝言の本文全文・相談と AI の答え・登録時の伝えたいこと**と状態ボタン〈対応中・保留・済み〉、メモ欄＋定型文 5 個＋「記録して済みにする」「記録だけ」。下にたたんで「質問と多い声」〈話題別の件数・多い質問・答えられなかった質問・AI で整理〉と「発言の一覧」〈絞り込み〉。`/staff/priority`・`/staff/voices` はここへ 303）／
  `/staff/handle` 対応タブ（受付番号→個人ページ・氏名→名簿検索・最近対応した人）／
  `/staff/checkin` 受付（避難者が PC の前で自分で入力する画面。運営のナビ・名簿・運営メモは出さない。受付番号を大きく出して「次の方へ」）／
  `/staff/roster` 名簿（検索・編集・退所・CSV。伝えたいこと・相談件数）／`/staff/roster/{id}` 個人ページ（**本人が伝えたいこと・対面対応のメモ・その人の相談**・バーコード）／
  `/staff/notices` お知らせ（投稿→AI 英訳→確認。`?category=&title=` で下書き）／
  `/staff/ai` AI 助手（①マニュアル RAG ②要配慮者 ③不足物資 ④掲示文 ⑤引継ぎ）／
  `/staff/sync` 本部へ送る／`/staff/settings` 設定（**想定地区の町名**を含む）・入口 QR・接続時の自動表示の状態・**名簿と履歴を白紙に戻す**（訓練・デモ前）・備蓄量・マニュアル再取り込み

受付番号は端末の署名付き Cookie（30 日）に持つ。同じスマホで開き直すとトップに戻り、バーコード（Code 128・受付番号 6 桁）は
サーバー側で SVG を作る（`python-barcode`）。読み取り側（カメラ・USB リーダー）は次の機能で入れる予定で、`static/zxing.min.js`（MIT）だけ同梱してある。

## API（個人側アプリとの契約・仕様書 §6）

| | |
|---|---|
| `GET /api/health` | `{ok, model, embed_model, warm, queue, ...}` |
| `GET /api/shelter` | 避難所の基本情報 |
| `GET /api/notices?lang=ja` | お知らせ（新しい順） |
| `POST /api/chat` | `{session, lang, message, history}` → SSE（`{"delta"}`… `{"queue": n}`… 最後に `{"done": true, "sources": [...]}`） |
| `POST /api/checkin` | `{source: "qr"|"manual"|"app", payload, staff_note?}` → `{id, status: created|duplicate|updated, evacuee}`。payload は §6.1 の JSON か、QR の文字列そのまま（`z:` 圧縮も可） |
| `POST /api/checkin/preview` | 登録せずに検証・重複確認だけ（受付画面の確認用。拡張） |
| `POST /api/checkout/{id}` | 退所（PIN） |
| `GET /staff/export/roster.csv` / `eei.json` | 名簿 CSV（標準様式順・BOM 付き）／ EEI 集計（PIN。想定地区・日本語以外・支援が要る世帯・対応の優先順の件数と上位〈受付番号・理由コード・時刻だけ〉も入る） |
| `GET /staff/api/summary` | ダッシュボードの数字（PIN）→ `{summary, supplies, health, breakdown, priority: {levels, open_total, anonymous, items(上位10)}}` |
| `GET /staff/api/priority?level=` | 対応の優先順の全行（PIN）→ `{levels, open_total, anonymous, items}` |
| `GET /staff/api/cases` | 未対応（受付済・対応中・保留）の「避難所に伝える」（PIN）→ `{items, categories, statuses}` |
| `POST /staff/cases/{id}/status` | 「伝える」の状態を変える（PIN・フォーム `status`=open/in_progress/hold/done・`back`） |
| `POST /staff/sync` | 本部へ送る（PIN）→ `{transport, sent, log_id}` |

受付 QR のサンプル画像: `python scripts/make_sample_qr.py` → `outputs/sample_checkin_*.png`（アプリ担当への共有・受付カメラの試験用）。

## 構成

```
shelter/
├ main.py          FastAPI（ルーティング・起動時ウォームアップ／マニュアル自動取り込み）
├ config.py        .env（SHELTER_* / SYNC_*）
├ captive.py       接続時にページを自動で開く（試験機能・既定 OFF。host_ip:53 の DNS と :80 の HTTP。ICS より優先される理由は冒頭のコメント）
├ db.py            SQLite（WAL）・設定値
├ schema/standard_form.py   標準様式の項目・区分コード・CSV 列順（ここ1か所）
├ checkin.py       ペイロード検証・二重登録判定（dev 一致／dev 無しは氏名＋生年月日 or 住所）
├ stats.py         集計・内訳（想定地区・日本語以外・支援が要る世帯・受付の方法）・不足物資のルール計算・名簿 CSV・EEI JSON
├ priority.py      対応の優先順（名簿の伝えたいこと・要配慮と避難者の声・「伝える」の未対応を人ごとに 1 行・3 段階。AI は使わない）
├ cases.py         避難所に伝える（案件表 cases。分類 8 種・レベル 3 段・状態 4 つ・同じ用件の再送は 1 件にまとめる。AI は使わない）
├ notices.py       お知らせ・AI 英訳
├ llm.py           AI 呼び出しの唯一の入口（生成 stream/complete・埋め込み embed。asyncio.Lock で直列・整理券で待ち人数・keep_alive=-1）
├ evidence.py      避難者チャットの根拠探し（案内・FAQ・お知らせを1行ずつ → bge-m3＋言葉の一致で近い行を選ぶ）
├ ai_evacuee.py    避難者チャット（FAQ とほぼ同じ質問は FAQ の答えをそのまま／それ以外は根拠4行だけで AI が「考えてから答える」2段階／答えの後処理／暴力・急病は決まった案内）
├ ai_staff.py      運営者の助手（RAG・要配慮者・物資・掲示文・引継ぎ・避難者の声の整理）
├ voices.py        避難者の声（チャットの発言を言葉の一致＋埋め込みの近さ（bge-m3・例文 EXAMPLES）で種類・話題に分類 → 集計・多い質問・対応状況。生成 AI は使わない）
├ rag.py           チャンク化（500字・重なり100）→ bge-m3 → BLOB → numpy コサイン
├ sync/            送り先の差し替え（file＝outbox 既定／http＝multipart POST／supabase＝スタブ）
├ web.py           PIN 認証（署名 Cookie 12h）・日英の文言・Markdown・SSE
├ barcode.py       受付番号のバーコード（Code 128・SVG。python-barcode）
├ templates/ static/   Jinja2＋素の JS（CDN 参照ゼロ。jsQR 1.4.0 同梱・Apache-2.0／zxing 0.21 同梱・MIT）
└ data/            shelter_info.{ja,en}.md・faq.{ja,en}.md・manual/・hazard/・local/・shelter.db
```

## 数え方（ダッシュボード・EEI）

受付1件＝代表者1人＋同行者（hh−1人）。同行者は名前・続柄・性別・年齢・要配慮・アレルギーを1人ずつ登録できる（`members`。受付番号は代表者と同じ）。
在所人数は hh の合計、世帯数は受付件数。性別・年齢・要配慮・アレルギーは代表者と登録した同行者を1人ずつ数え、名前を登録していない同行者だけ性別「不明」に数える。
言語は受付（世帯）単位。乳幼児＝3歳未満の人（その世帯に3歳未満の登録が無く代表者が「乳幼児同伴」なら1人）。名簿 CSV は1人1行（同行者は同じ受付番号で続き、「続柄(拡張)」に続柄）。

内訳: 想定地区＝住所に設定の町名（既定 `東野田町,Higashinoda`）を含む世帯（人数ベースの %・住所なしは別）。日本語以外＝世帯の言語が日本語以外か「日本語がわからない」を選んだ世帯（二重に数えない）。支援が要る世帯＝伝えたいことか要配慮（本人・同行者）のある世帯。
対応の優先順: 1 行のレベル＝理由の最大値（レベル表は `priority.py` と [docs/dev/dashboard_stats.md](../docs/dev/dashboard_stats.md) §3.2）。名簿由来の理由は「最新の対面対応メモが登録内容の更新（`updated_at`）以降にあれば対応済み」、相談は `status='open'` の要望・困りごと・緊急だけ。対面対応のメモを書くとその人の未対応の相談・「伝える」も対応済みになる（メモでは `updated_at` を進めない）。「伝える」の対応中・保留は理由に状態を添える（例「伝える: 医療・体調（対応中）」）。

## テスト

```powershell
.\.venv\Scripts\python.exe scripts\shelter_smoke.py            # 一時 DB で内部起動（AI 込み）
.\.venv\Scripts\python.exe scripts\shelter_smoke.py --no-ai    # AI 以外だけ（数秒）
.\.venv\Scripts\python.exe scripts\shelter_smoke.py --url http://localhost:8000   # 起動中のサーバー
.\.venv\Scripts\python.exe scripts\check_evidence.py           # 避難者チャットの根拠の当たり方（しきい値の調整用・埋め込みだけ）
.\.venv\Scripts\python.exe scripts\check_chat.py               # 起動中のサーバーに日英13問を投げ、答えと秒数（考え／最初／全体）を見る（テスト用お知らせを入れて最後に消す。`--notice-only` でお知らせの3問だけ）
.\.venv\Scripts\python.exe scripts\shelter_smoke.py --gemini   # Gemini でも chat とマニュアル RAG を1回ずつ
```

Git Bash の `curl` で日本語の JSON を送ると文字化けして 400 になる。日本語の API テストは Python（上のスクリプト）で送る。

## 設定（.env）

`.env.example` の「避難所AI」欄を参照。避難所名・IP・SSID・同期先は画面（設定／本部へ送る）からも変えられ、画面の値が優先。
チャットモデルの既定は `qwen3:1.7b`（GPU 無しのノートPC向け）。qwen3.5 系は質問が変わるたびに前置きを全部読み直すため、CPU だと 1 問に数分かかった。
GPU のある PC なら `SHELTER_CHAT_MODEL=qwen3.5:4b` でもよい。埋め込みを軽くするなら `SHELTER_EMBED_MODEL=embeddinggemma`（変えたらマニュアルを取り込み直し、`evidence.py` のしきい値も見直す）。

GPU 無し PC での目安（2026-09-25・Intel UHD・RAM 16GB・qwen3:1.7b）: FAQ にある質問 約3秒／それ以外は「考え」11〜19秒＋答え、全体 14〜27秒（根拠が無いと考えたときは生成せず 12〜18秒）。
Gemini API への切り替えは下の「クラウド AI（Gemini）に切り替える」（設計は [docs/dev/gemini_backend.md](../docs/dev/gemini_backend.md)）。

## クラウド AI（Gemini）に切り替える

ネットがあるとき（Starlink 到達後など）や、答えの質を見比べたいときに、同じ画面のまま Google Gemini API で答えさせられる。

1. `.env` に API キーを書いて、サーバーを起動し直す（キーは画面・DB には保存しない）

   ```
   GEMINI_API_KEY=（Google AI Studio で発行したキー）
   GEMINI_MODEL=gemini-3.8-flash       # 省略可
   GEMINI_THINKING_BUDGET=0            # 省略可。0 = 思考トークンを使わない
   ```

2. `.\.venv\Scripts\python.exe scripts\check_gemini.py` で、使えるモデルの一覧と往復時間を確かめる（運営者画面の「設定 → クラウド AI」の「接続テスト」でも可）
3. 避難者チャットと AI 助手の右上に「この PC の AI ⇄ クラウド AI (Gemini)」のスイッチが出る。状態は端末ごと（ブラウザの localStorage）。キーが無ければスイッチは出ず、すべてこの PC で動く

決まりごと:

- 根拠（お知らせ・案内・FAQ・マニュアル抜粋）はローカルと同じものを渡す。**生成だけ**が Gemini になる（避難者チャットの「考え → 答え」の2段階も Gemini で同じように動く）
- **要配慮者の洗い出し（名簿）はスイッチに関係なく常にこの PC**。氏名やメモを外に出さないため
- 埋め込み（`bge-m3`）・お知らせの英訳・起動時のウォームアップは Ollama のまま。**Gemini モードでも Ollama は起動しておく**（`run_shelter.ps1` はそのまま）
- Gemini が失敗しても自動でローカルには戻さない。エラー文で「右上のスイッチで『この PC の AI』に戻す」よう案内する
- 見比べ用に、避難者チャットの吹き出しに「PC」「Gemini」の小さなタグが付く（FAQ の直答・決まった案内は AI を通らないので付かない）

確認用: `scripts\check_chat.py --backend gemini`、`scripts\shelter_smoke.py --gemini`（キーが無ければ Gemini の項目は飛ばす）。

## 運用上の注意

- 個人情報は `shelter/data/shelter.db` と `shelter/outbox/` にだけある（どちらも git 対象外）。PC は BitLocker を有効にし、離席時はロック。持ち出した CSV は outbox から削除
- 避難所の案内（`data/shelter_info.*.md`）の設備・場所はデモ用の想定。実運用では書き換える
- AI の回答は参考。避難者チャットは PC の中で「考える（使う根拠の番号と要点）→ 書く」の2段階で答え、根拠の数行に無いことは生成せずに「その情報はまだありません。受付で確認してください」と答える（答えに混ざったら後処理でその文だけにする）。暴力・性被害・急病はキーワードで AI を通さず決まった案内を返す
- デモでよく聞く質問は `data/faq.{ja,en}.md` に足しておくと、AI を通さず約3秒で書いたとおりに返る
