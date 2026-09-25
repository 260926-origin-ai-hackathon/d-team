# 避難所AI

避難者の受付・相談・要望を一か所に集め、避難所の運営者が対応すべき順に確認できる、ローカルファーストの避難所運営支援システムです。

普通のWindowsノートPCを避難所内のサーバーとして使い、避難者は自分のスマートフォンからアクセスします。インターネットが不安定な状況でも、受付、名簿、お知らせ、AI相談、要望管理を同じローカルネットワーク内で継続できます。

Business AI Hackathon Origin 2026 Vol.2で制作したプロトタイプです。

## 解決したい課題

災害時の避難所には、受付情報、健康状態、必要な物資、個別の相談、運営からのお知らせなど、種類の異なる情報が短時間に集まります。紙や口頭だけで扱うと、同じ内容の聞き直し、対応漏れ、情報の分散が起きやすくなります。

避難所AIは、避難者が自分で情報を入力できる入口と、運営者が全体を把握する画面を一つのPCで提供します。AIは判断を代行するのではなく、案内の検索、回答の下書き、相談内容の整理を支援します。

## 主な機能

### 避難者向け

- スマートフォンからの自己受付と同行者登録
- 受付番号とバーコードの表示
- 日本語・英語のお知らせ閲覧
- お知らせ、FAQ、施設案内を根拠にしたAI相談
- 医療、移動、食事、安全などの要望送信
- 送信した要望の対応状況確認
- 日本語・英語の切り替え

### 運営者向け

- 在所人数、世帯数、要配慮情報のダッシュボード
- 「至急」「今日中」「通常」の対応優先順
- 相談、要望、登録時の申し出を人ごとに集約
- 対応中、保留、対応済みの状態管理と対応メモ
- 名簿検索、編集、退所処理、CSV出力
- お知らせ作成と英訳確認
- 運営マニュアルを参照するAI助手
- 名簿CSVと集計JSONの本部向け書き出し

### AI・データ基盤

- 生成はOllama上のローカルLLMを既定で使用
- `bge-m3`の埋め込み検索で、質問に近い根拠だけを選択
- 急病や暴力に関する入力は生成AIを通さず、窓口への案内を優先
- ローカル生成が遅い場合に限り、任意でGeminiへ切り替え可能
- 個人情報、名簿、相談履歴はSQLiteに保存

## 利用の流れ

1. 避難者がスマートフォンから本人と同行者を登録します。
2. システムが受付番号とバーコードを発行します。
3. 避難者はお知らせを確認し、AIへ質問するか、運営へ要望を送ります。
4. AIが質問に関係する根拠を探し、回答を作成します。
5. 要望と相談内容を分類し、対応の優先順へ反映します。
6. 運営者がダッシュボードで内容を確認し、対応状況とメモを記録します。
7. 必要に応じて名簿CSVと集計JSONを本部向けに書き出します。

## システム構成

| 領域 | 使用技術 |
|---|---|
| Webアプリ | FastAPI、Jinja2、JavaScript、CSS |
| データベース | SQLite（WAL） |
| ローカル生成AI | Ollama、`qwen3:1.7b` |
| 埋め込み検索 | Ollama、`bge-m3`、NumPy |
| 任意のクラウドAI | Google Gemini API |
| バーコード・QR | python-barcode、qrcode、同梱JavaScriptライブラリ |
| 配布環境 | Windows 10/11、避難所内Wi-Fiまたは同一LAN |

1台のPCで、避難者向けページ、運営者向け画面、API、AI、データベースをまとめて動かします。ブラウザがあれば利用できるため、避難者のスマートフォンへのアプリインストールは不要です。

## クイックスタート

### 必要なもの

- Windows 10またはWindows 11
- Python 3.10以上
- メモリ8GB以上を推奨
- 初回セットアップ用のインターネット接続
- 8GB程度の空き容量

### 1. セットアップ

PowerShellで実行します。

```powershell
git clone https://github.com/naka6ryo/local-ai-hackathon.git
cd local-ai-hackathon
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

セットアップスクリプトは、Ollamaの確認、モデルの取得、Python仮想環境の作成、依存パッケージのインストール、`.env`の作成を行います。

環境を先に確認する場合は、次を実行してください。

```powershell
powershell -ExecutionPolicy Bypass -File scripts\check_env.ps1
```

### 2. 起動

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_shelter.ps1
```

初回起動では、モデルの読み込みやマニュアルの取り込みに時間がかかります。デモや訓練では、利用開始の5分前までに起動してください。

### 3. 画面を開く

| 利用者 | URL | 備考 |
|---|---|---|
| 避難者 | `http://localhost:8000/` | PC上での確認用 |
| 避難者 | `http://192.168.137.1:8000/` | Windowsモバイルホットスポットの既定IP |
| 運営者 | `http://localhost:8000/staff` | 既定PINは`1234` |
| 受付端末 | `http://localhost:8000/staff/checkin` | PCのカメラを使う場合はlocalhostで開く |
| ヘルスチェック | `http://localhost:8000/api/health` | `warm: true`でAIの準備完了 |

運営者PINはデモ用の初期値です。実運用や共有環境では、`.env`の`SHELTER_STAFF_PIN`を必ず変更してください。

## スマートフォンから接続する

PCとスマートフォンを同じネットワークに接続し、PCのIPアドレスとポート`8000`をスマートフォンのブラウザで開きます。

Windowsモバイルホットスポットの既定IPは`192.168.137.1`です。ただし、Windowsのモバイルホットスポットは共有元のネット接続がないと有効にできません。インターネットのない環境でローカルネットワークだけを作る場合は、トラベルルーターやスマートフォンのテザリングを利用し、`.env`の`SHELTER_HOST_IP`をPCのIPに変更してください。

入口用のWi-Fi QRとURL QRは、運営画面の「設定」から印刷できます。

## 設定

初回セットアップ時に`.env.example`から`.env`が作られます。主な項目は次のとおりです。

| 設定 | 既定値 | 用途 |
|---|---|---|
| `SHELTER_NAME` | `東高等学校 避難所` | 避難所名 |
| `SHELTER_HOST_IP` | `192.168.137.1` | スマートフォン向け入口URL |
| `SHELTER_PORT` | `8000` | Webサーバーのポート |
| `SHELTER_STAFF_PIN` | `1234` | 運営者ログインPIN |
| `SHELTER_CHAT_MODEL` | `qwen3:1.7b` | ローカル生成モデル |
| `SHELTER_EMBED_MODEL` | `bge-m3` | 埋め込みモデル |
| `GEMINI_API_KEY` | 空 | 設定時のみGemini切り替えを表示 |
| `SYNC_TRANSPORT` | `file` | 本部共有方法。`file`または`http` |

設定項目の全体は[`.env.example`](.env.example)を参照してください。避難所名、住所、Wi-Fi情報、想定地区などは運営画面からも変更できます。

## デモデータ

訓練・デモ用の名簿、相談、要望を投入できます。

```powershell
.\.venv\Scripts\python.exe scripts\seed_demo.py --reset
```

実データが入っている環境では`--reset`を使わないでください。名簿、相談履歴、対応記録が削除されます。

## テスト

```powershell
# AIを呼ばない主要機能のスモークテスト
.\.venv\Scripts\python.exe scripts\shelter_smoke.py --no-ai

# 根拠検索の確認
.\.venv\Scripts\python.exe scripts\check_evidence.py

# 起動中のサーバーに日英の質問を送り、回答時間を確認
.\.venv\Scripts\python.exe scripts\check_chat.py
```

スターターキット部分だけを確認する場合は、次を実行します。

```powershell
.\.venv\Scripts\python.exe scripts\smoke_test.py
```

## ディレクトリ構成

```text
local-ai-hackathon/
├─ shelter/                 避難所AI本体
│  ├─ templates/            避難者・運営者画面
│  ├─ static/               CSS、JavaScript、QR読取ライブラリ
│  ├─ data/                 FAQ、施設案内、運営マニュアル
│  ├─ sync/                 file、HTTP、本部同期の実装
│  ├─ main.py               FastAPIアプリとルーティング
│  ├─ llm.py                Ollama・Geminiの共通AI呼び出し
│  ├─ rag.py                マニュアル取り込みと検索
│  ├─ priority.py           対応優先順の計算
│  └─ db.py                 SQLiteと設定値
├─ scripts/                 セットアップ、起動、デモ、テスト
├─ docs/
│  ├─ spec/                 実装仕様書
│  ├─ research/             避難所運営に関する調査
│  ├─ pitch/                自治体向け資料と事業検討
│  └─ dev/                  設計メモ
├─ localai/                 汎用ローカルLLMスターターキット
├─ app.py                   スターターキットのStreamlit UI
└─ .env.example             設定例
```

## データと安全性

- 既定では、AI生成と埋め込み検索をPC内のOllamaで実行します。
- 名簿、受付情報、相談履歴、対応メモは`shelter/data/shelter.db`に保存します。このファイルはGit管理対象外です。
- `GEMINI_API_KEY`を設定してクラウドAIへ切り替えた場合、入力内容が外部APIへ送信されます。個人情報を含む運用では利用条件と送信内容を確認してください。
- AIの回答は参考情報です。医療、安全、避難判断の最終判断は運営者と関係機関が行ってください。
- 同期の既定値`file`は、`shelter/outbox/`へ名簿CSVと集計JSONを書き出します。`http`は指定URLへの送信に対応しています。Supabase送信は未実装です。

## 現在の制約

- ハッカソンで制作したプロトタイプであり、本番の防災業務で必要な可用性、監査、権限分離、暗号化をすべて満たすものではありません。
- CPUのみのPCでは、ローカルAIの回答に十数秒以上かかる場合があります。
- キャプティブポータルは端末ごとの挙動差が大きいため既定で無効です。Wi-Fi QRとURL QRの2段階案内を推奨します。
- 本部側の統合システムは含まれていません。現時点ではファイル書き出しまたはHTTP送信を利用します。

## 関連ドキュメント

- [避難所AIの詳細な起動・画面・API](shelter/README.md)
- [避難所AI 仕様書 v1.0](docs/spec/避難所AI_仕様書_v1.0.md)
- [個人アプリ仕様書 v1.0](docs/spec/個人アプリ_仕様書_v1.0.md)（現在は保留）
- [Geminiバックエンド設計](docs/dev/gemini_backend.md)
- [モデル選択](docs/models.md)
- [トラブルシューティング](docs/troubleshooting.md)
- [調査資料](docs/research/README.md)

## 開発の背景

このリポジトリは、WindowsノートPCでOllamaを簡単に試すための`localai/`スターターキットから始まりました。ハッカソン期間中に、避難所での情報整理と対応支援を目的とした`shelter/`アプリを追加し、現在はこちらがプロジェクトの中心です。スターターキットのStreamlit UIとサンプルも引き続き利用できます。

## License

[MIT License](LICENSE)
