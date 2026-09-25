# local-ai-hackathon

普通の Windows ノートPC（GPU なし・RAM 8GB〜）で **ローカルLLM を動かす** ためのスターターキット。
ビジネスAIハッカソン向けに、「入れる → 動く → 業務例を試す → 自分のアプリに組み込む」を最短にしています。

- 推論エンジン: [Ollama](https://ollama.com/)（インストーラ1本、CPU/GPU 自動切替）
- 既定モデル: `qwen3.5:4b`（3.4GB・日本語対応・画像入力・JSON出力・ツール呼び出し可）
- 付属: ブラウザ UI（Streamlit）、ターミナルチャット、要約・構造化抽出のサンプル
- データはすべて PC 内で処理され、外部に送信されません

> **避難所AI（防災ローカルAI の避難所PC側）** はこのキットの上に `shelter/` として作っています。
> 起動は `powershell -ExecutionPolicy Bypass -File scripts\run_shelter.ps1`、詳細は [shelter/README.md](shelter/README.md)。
> 個人側（住民のスマホで動く PWA）の仕様は [docs/spec/個人アプリ_仕様書_v1.0.md](docs/spec/個人アプリ_仕様書_v1.0.md)（**保留**: 今回の提出は避難所AIのみ。実装するときは `app/` に置く）。

## 1. セットアップ（初回のみ・約10分）

前提: Windows 10/11、Python 3.10 以上（[python.org](https://www.python.org/downloads/) で「Add python.exe to PATH」にチェック）、空きディスク 5GB。

PowerShell でこのフォルダを開いて:

```powershell
git clone https://github.com/naka6ryo/local-ai-hackathon.git
cd local-ai-hackathon
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

これで Ollama のインストール → モデルのダウンロード → Python 仮想環境の作成まで自動で行います。

- RAM が 8GB 未満のPC: `scripts\setup.ps1 -Model gemma3:1b`（815MB）
- 自分のPCで動くか先に見たい: `powershell -ExecutionPolicy Bypass -File scripts\check_env.ps1`

## 2. 動かす

| やりたいこと | コマンド |
|---|---|
| 動作確認（速度も出る） | `.\.venv\Scripts\python.exe scripts\smoke_test.py` |
| ブラウザでチャット | `powershell -ExecutionPolicy Bypass -File scripts\run_ui.ps1` → http://localhost:8501 |
| ターミナルでチャット | `.\.venv\Scripts\python.exe -m localai.chat_cli` |
| 議事録を要約 | `.\.venv\Scripts\python.exe examples\summarize.py examples\sample_minutes.txt` |
| 文章から JSON 抽出 | `.\.venv\Scripts\python.exe examples\extract_json.py "山田です。来週火曜14時にA社と打合せ。3名参加。"` |

最初の1回だけモデルの読み込みで 30〜60 秒待ちます。2回目以降は数秒で返ります。

## 3. 自分のアプリに組み込む

```python
from localai import LocalAI, chat

# 使い捨て
print(chat("この文を敬語に直して: 明日休みます"))

# 会話履歴つき
ai = LocalAI(system_prompt="あなたは社内ヘルプデスクです。")
print(ai.ask("有給の申請方法は？"))
for piece in ai.ask_stream("もっと簡潔に"):   # ストリーミング
    print(piece, end="")
```

- 画像を渡す: `ai.ask("この領収書の金額は？", images=["receipt.png"])`
- 必ず JSON で受け取る: [examples/extract_json.py](examples/extract_json.py)（Ollama の `format=スキーマ` を使用）
- モデルやシステムプロンプトの既定値は `.env` で変更（[.env.example](.env.example) を参照）
- Ollama は OpenAI 互換 API（`http://localhost:11434/v1`）も持つので、既存の OpenAI SDK 製コードもほぼそのまま動きます

## 4. モデルの選び方

| モデル | サイズ | 目安 RAM | 特徴 |
|---|---|---|---|
| `gemma3:1b` | 0.8GB | 4GB〜 | とにかく軽い。要約・分類向け。画像不可 |
| `qwen3.5:2b` | 2.7GB | 8GB〜 | 軽さと日本語品質のバランス。画像可 |
| **`qwen3.5:4b`** | 3.4GB | 8GB〜 | **既定**。日本語が安定、画像・JSON・ツール対応 |
| `qwen3.5:9b` | 6.6GB | 16GB〜 | 品質重視。GPU ありなら快適 |

切替は `ollama pull <モデル名>` → `.env` の `LOCALAI_MODEL` を書き換え（UI ならサイドバーで選択）。
詳しくは [docs/models.md](docs/models.md)。

## 5. 困ったとき

[docs/troubleshooting.md](docs/troubleshooting.md) を参照。よくあるのは:

- `Ollama サーバーに接続できません` → スタートメニューから Ollama を起動（タスクトレイにアイコンが出ます）
- 遅い → 軽いモデルに変更、他のアプリを閉じる、`.env` の `LOCALAI_NUM_CTX` を 4096 に下げる
- `python` が見つからない → Python インストール時に PATH にチェックを入れて再インストール

## 構成

```
localai/          # ライブラリ本体（config / client / chat_cli）
app.py            # Streamlit チャット UI
examples/         # 業務例（要約・JSON抽出）
scripts/          # setup / run_ui / check_env / smoke_test
docs/             # モデル選び・トラブルシュート
```

## License

MIT
