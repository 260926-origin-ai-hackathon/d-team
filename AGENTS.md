# AGENTS.md — このリポジトリで作業する AI 向けの案内

## 何のリポジトリか

- `localai/`・`app.py`: Windows ノートPC で Ollama を動かすスターターキット（最初からある。基本は変更しない）
- `shelter/`: **避難所AI**（避難所の PC 1台で受付・名簿・お知らせ・日英チャット・運営者 AI を動かす FastAPI アプリ）。いま開発しているのはこちら
- ブランチ: 開発は `shelter-ai`。`main` はキットの初期構築だけ

最初に読むもの:
1. [shelter/README.md](shelter/README.md) — 起動・画面・API・構成・テスト
2. [docs/spec/避難所AI_仕様書_v1.0.md](docs/spec/避難所AI_仕様書_v1.0.md) — 仕様（§9 が AI の設計、§11 が個人情報、§19 が変更履歴）
3. Gemini API への切り替えをするなら [docs/dev/gemini_backend.md](docs/dev/gemini_backend.md)

## 動かし方（Windows・PowerShell）

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1        # 初回だけ（Ollama・venv・モデル）
powershell -ExecutionPolicy Bypass -File scripts\run_shelter.ps1  # 起動 → http://localhost:8000/staff（PIN 1234）
```

起動後 1〜2 分は準備中（`/api/health` の `warm` が true になるまで）。止めるときは 8000 番で待ち受けているプロセスを止める。

## 確かめ方

```powershell
.\.venv\Scripts\python.exe scripts\shelter_smoke.py --no-ai   # AI 以外（数秒）
.\.venv\Scripts\python.exe scripts\check_evidence.py          # 避難者チャットの根拠探し（埋め込みだけ）
.\.venv\Scripts\python.exe scripts\check_chat.py              # 起動中のサーバーに日英の質問（答えと秒数）
```

変更したら、少なくとも関係するものを流して結果を報告する。AI の答えの良し悪しは `check_chat.py` の出力で比べる。

## AI まわりの決まり

- **AI の呼び出しは `shelter/llm.py` の `stream()`（生成）と `embed()`（埋め込み）だけ**。ほかのファイルから `ollama` を直接呼ばない
- 既定はオフライン（Ollama）。チャットは `qwen3:1.7b`、埋め込みは `bge-m3`（`.env` の `SHELTER_CHAT_MODEL` / `SHELTER_EMBED_MODEL`）
- 埋め込みモデルを変えるとマニュアルの取り込み直しと `evidence.py` のしきい値の見直しが要る
- 避難者チャットは「根拠を選んで渡す」方式（仕様書 §9.2）。資料を丸ごとプロンプトに入れる形に戻さない
- 暴力・急病のガード（`ai_evacuee.guard`）は AI を通さない。外さない
- **個人情報を PC の外に出さない**（仕様書 §11）。外部 API（Gemini など）を使う変更では、`ai_staff._care` が氏名・メモを AI に渡している点に必ず対応する（[docs/dev/gemini_backend.md](docs/dev/gemini_backend.md) §4）

## この環境で踏んだ罠

- **PowerShell 5.1**: `.ps1` は UTF-8（BOM 付き）で保存する（BOM が無いと日本語が壊れる）。`Get-Content` / `Set-Content` は `-Encoding utf8` を付ける（付けないと `.env` が文字化けした）
- **Git Bash の `curl` で日本語の JSON を送ると文字化けして 400**。日本語の API テストは Python で送る
- **CPU だけの PC では生成が遅い**（qwen3:1.7b で 1 問 13〜47 秒）。qwen3.5 系は質問が変わるたびに前置きを全部読み直すので、CPU では使わない
- Windows のモバイルホットスポットは、共有元のネット接続が無いと ON にできない。また端末が無いと数分で自動 OFF になる（「省電力」を切る）
- Windows ホットスポット（ICS）の DNS 中継は 0.0.0.0:53 で待つ。`192.168.137.1:53` に bind すると自前 DNS が優先される（`shelter/captive.py` がこれで「Wi-Fi に入るだけでページが開く」を実現。管理者権限は不要、ファイアウォールの許可は要る）。ただし出方が機種で揃わないので**既定 OFF**・入口ポスターは QR 2 枚（`SHELTER_CAPTIVE=1` で試せる）
- `.env`・`shelter/data/shelter.db`・`shelter/outbox/`・公開データの原本（PDF 等）は git に入れない（`.gitignore` 済み）。API キーは `.env` にだけ書く

## 書き方

- コメント・文書・画面の文言は日本語（避難者向けは日英）。周りのコードの書き方に合わせる
- 仕様を変えたら、仕様書の該当節と §19 の変更履歴、`shelter/README.md` を合わせて直す
