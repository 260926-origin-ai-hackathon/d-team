# Gemini API への切り替え（実装ガイド）

> **状態: 実装済み（`feature/gemini-backend` ブランチ `617ecef`）**。使い方は [shelter/README.md](../../shelter/README.md) の「クラウド AI（Gemini）に切り替える」。
> 実装はこの設計と一部ちがう: `.env` の切り替えではなく**画面のスイッチでリクエストごと**に選ぶ／Gemini が失敗しても**Ollama へは自動で戻さない**／要配慮者の洗い出し（`care`）は常にローカル。
> 以下は実装前に書いた引き継ぎ資料（2026-09-25・`shelter-ai` ブランチ `4dfb56a` の次）。

## 1. 目的と前提

- **目的**: デモのときだけ、チャット・生成を Gemini API に切り替えられるようにする（CPU のノートPCだと Ollama が遅いため）
- **前提は変えない**: 製品の主張は「ネットが無くても避難所の PC 1台で動く」。**既定は Ollama のまま**にし、Gemini は `.env` で明示したときだけ使う
- ネットが切れても落ちないこと（Gemini が失敗したら Ollama に戻る）

## 2. AI を呼んでいる場所（ここ以外に呼び出しは無い）

生成（チャット）はすべて **`shelter/llm.py` の `stream()` 1か所**を通る。埋め込みは `embed()` 1か所。

| 呼び出し元 | 関数 | 用途 | 流し方 |
|---|---|---|---|
| `main.py` `api_chat` → `web.sse_llm` | `llm.stream` | 避難者チャット | 全文を受け取ってから `finalize`（`ai_evacuee.clean_answer`）で整えて送る |
| `main.py` `api_staff_ai` → `web.sse_llm` | `llm.stream` | 運営者 AI 助手（5タスク） | 少しずつ流す（SSE） |
| `notices.translate_to_en` | `llm.complete`（中で `stream`） | お知らせの英訳 | 全文 |
| `ai_evacuee.prime_cache` | `llm.complete` | 起動時の前置きキャッシュ作り（Ollama 専用の高速化） | — |
| `llm.warmup` | `llm.complete` / `llm.embed` | 起動時にモデルを載せる | — |
| `evidence.py`（3か所） | `llm.embed` | 避難者チャットの根拠探し | — |
| `rag.py` `ingest` / `search` | `llm.embed` | 運営マニュアルの取り込み・検索 | — |

`web.sse_llm` は `llm.stream(messages, max_tokens, meta=meta)` を呼び、`meta["ticket"]` と `llm.position()` で待ち人数を画面に出す。

### 2.1 `llm.stream()` の約束（切り替えても守る）

- 入力 `messages`: `[{"role": "system"|"user"|"assistant", "content": str}, ...]`。**system は先頭に1つだけ**
- 出力: 文字列の断片を `yield` する非同期ジェネレーター
- `asyncio.Lock` で1本ずつ実行し、`_waiting` と整理券で待ち人数を出す
- `_state`（`busy` / `warm` / `last_error` / `last_tps`）を更新する。`/api/health` と運営者ダッシュボードがこれを表示する
- `<think>…</think>` が混ざっても捨てる（Gemini では出ないはずだが、そのままでよい）
- 例外は `_state["last_error"]` に入れて投げ直す（`sse_llm` がエラー表示にする）
- **`plan`（`llm.Plan`）を渡されたら2段階で生成する**: まず `plan.max_tokens` 以内で「考え」を作り、`plan.followup(考え)` が返した messages で答えを生成する（str が返ったら生成せずその文を `yield`）。段階を `meta["phase"]`（`"think"` → `"write"`）に、考えを `meta["plan"]` に入れる。避難者チャットはこれで「使う根拠の番号」を先に決めさせている（仕様書 §9.2 の 4）。Gemini でも同じ2回呼びでよい（2回目は1回目の会話＋考え＋「答えだけ書いて」）

## 3. 推奨する設計

### 3.1 切り替えるのは「生成」だけ。埋め込みは Ollama（bge-m3）のまま

- マニュアル 512 チャンクと根拠の行は **bge-m3 のベクトル**で保存・比較している。埋め込みを Gemini に変えると、全部の取り込み直しが要り、しきい値（`evidence.py` の `FAQ_DIRECT` など。bge-m3 で調整済み）も合わなくなる
- 埋め込みは CPU でも 1 問 1 秒未満なので、遅さの原因ではない（遅いのは生成）

### 3.2 設定（`.env`・`shelter/config.py`）

案（名前は実装時に決めてよい。決めたら `.env.example` とこの文書を合わせる）:

```ini
# 生成の送り先: ollama（既定・オフライン）| gemini（デモ用・ネットが要る）
SHELTER_LLM_BACKEND=ollama
GEMINI_API_KEY=            # .env にだけ書く。.env は .gitignore 済み。コミットしない
GEMINI_MODEL=              # Flash 系の軽いモデル。名前は実装時に公式ドキュメントで確認する
# Gemini が失敗したら Ollama で答え直す（ネットが切れたとき用）
SHELTER_LLM_FALLBACK=1
```

`config.py` の `Config` に同じ名前の項目を足す（`_env()` で読む）。

### 3.3 `llm.py` の変え方

- `stream()` の中の Ollama 呼び出し（`_client().chat(...)` の部分）を、`config.llm_backend` で分ける
  - `ollama`: 今のまま
  - `gemini`: Gemini の SDK でストリーミング生成する
- **メッセージの変換**: 先頭の system → Gemini の system instruction。`user` → `user`、`assistant` → `model`
- **オプションの対応**: `temperature` → そのまま。`num_predict`（`max_tokens`）→ 出力トークン上限。`num_ctx` と `keep_alive` → Gemini では不要
- **考える処理（thinking）**: 今は `think=False`（モデル内蔵の思考は使わず、アプリ側の `Plan` で「考え→答え」を分けている）。Gemini でも内蔵の思考量を 0／最小にする設定があれば使う（無ければ、出力トークン上限を少し大きめにする）。設定名はモデルと SDK の版で違うので、実装時に確認する
- **ロック**: Gemini はネット越しなので同時に投げても PC は重くならない。ただ、待ち人数の表示は `_lock` に頼っているので、最初はロックをそのまま使うのが安全
- **失敗時**: `SHELTER_LLM_FALLBACK=1` なら、Gemini の例外（ネット無し・タイムアウト・レート制限）のときに、同じ `messages` で Ollama を呼び直す。`_state["last_error"]` には Gemini のエラーを残す
- **`status()`**: `model` に実際の送り先が分かる値を出す（例: `gemini:<モデル名>`）。`backend` の項目も足すと、ダッシュボードで見分けやすい
- **`warmup()`**: Gemini のときは生成のウォームアップは不要（1回投げて疎通を見る程度でよい）。埋め込みのウォームアップは残す

### 3.4 `ai_evacuee.prime_cache()`

Ollama の前置きキャッシュを作るためのもの。Gemini のときは、**`evidence.warm()`（根拠の埋め込み）だけ実行し、`llm.complete` は飛ばす**。

### 3.5 依存パッケージ

`requirements.txt` に Gemini の公式 Python SDK を足す（パッケージ名は実装時に公式ドキュメントで確認する）。Ollama だけで使う人の邪魔にならないよう、**`import` は Gemini を使うときだけ**にする（関数の中で import する）。

## 4. 個人情報（必ず対応する）

Gemini に切り替えると、生成に渡した文が **PC の外（Google）に出る**。仕様書 §11「個人情報と AI 処理は PC の外に出ない」と食い違うので、次を守る。

| 呼び出し | 渡している中身 | Gemini のときの扱い |
|---|---|---|
| 運営者 AI「要配慮者」`ai_staff._care` | **氏名・年齢・要配慮の区分・アレルギー・メモ**（`r['name']`, `r['note']`） | **氏名とメモを外し、受付番号だけにする**か、このタスクだけ常に Ollama で動かす。どちらかを必ず入れる |
| 運営者 AI「引継ぎ」`_handover` | 人数の集計・お知らせ・相談の傾向（件数）・運営者メモ（自由記述） | 集計は外に出してよい。運営者メモに個人名を書かないよう画面に注意書き |
| 運営者 AI「マニュアル」「物資」「掲示文」 | マニュアル抜粋・集計・運営者の入力文 | 問題なし（入力文に個人名を書かない注意だけ） |
| 避難者チャット | 根拠の数行と質問文（チャットログは匿名。氏名は渡していない） | 質問文に避難者が個人情報を書く可能性はある。Gemini のときは画面に「インターネット上の AI を使っています」と出す |
| お知らせの英訳 | お知らせの本文 | 問題なし |

そのほか:
- 画面（避難者のトップ・運営者ダッシュボード）に、**いまクラウドの AI を使っていること**が分かる表示を出す。デモで「オフラインで動く」と説明するときに、どちらで動いているかを取り違えないため
- API キーはログ・画面・`/api/health` に出さない

## 5. 変えないもの

- 避難者チャットの仕組み（`evidence.py` で根拠を選ぶ → FAQ とほぼ同じ質問は AI を通さず返す → 根拠の数行だけで AI に答えさせる → `clean_answer` で整える）。Gemini は賢いので資料を丸ごと渡しても答えられるが、**根拠を絞る方式は残す**（答えが資料から外れない・Ollama に戻っても同じ動きになる）
- 暴力・急病のガード（`ai_evacuee.guard`。AI を通さない）
- 受付・名簿・同期・画面

## 6. 確かめ方

```powershell
# 1) Ollama のまま（今までどおり動くこと）
.\.venv\Scripts\python.exe scripts\check_chat.py

# 2) .env で SHELTER_LLM_BACKEND=gemini にしてサーバーを再起動し、同じテスト
.\.venv\Scripts\python.exe scripts\check_chat.py
#   → 答えの中身が案内・FAQ・お知らせどおりか、時間がどれだけ縮んだかを比べる

# 3) Gemini のままネットを切る（Wi-Fi の共有元を外す等）→ Ollama に戻って答えること
# 4) 運営者 AI の「要配慮者」で、Gemini に氏名・メモが渡っていないこと（渡す直前の messages をログで確認）
```

`scripts/check_evidence.py` は埋め込みだけを見るので、切り替えの影響を受けない（受けたら 3.1 に反している）。

## 7. 実装チェックリスト

- [ ] `config.py` に `llm_backend` / `gemini_api_key` / `gemini_model` / `llm_fallback`
- [ ] `llm.py` の `stream()` を送り先で分岐（メッセージ変換・オプション対応・失敗時に Ollama へ）
- [ ] `llm.status()` に送り先を出す
- [ ] `llm.warmup()` と `ai_evacuee.prime_cache()` を Gemini のとき用に分ける
- [ ] `ai_staff._care` で Gemini に氏名・メモを渡さない
- [ ] 画面に「クラウドの AI を使用中」の表示
- [ ] `requirements.txt`（Gemini の SDK）・`.env.example`（§3.2 の項目。キーは空欄）
- [ ] `shelter/README.md` の「設定」、仕様書 §4.1・§9.1・§11・§17・§19 を更新
- [ ] §6 の確かめ方を全部通す
