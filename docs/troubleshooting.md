# トラブルシューティング

## 「Ollama サーバーに接続できません」

- スタートメニューから **Ollama** を起動（タスクトレイにラマのアイコンが出れば OK）
- または PowerShell で `ollama serve`
- 確認: ブラウザで http://localhost:11434 を開くと `Ollama is running` と出る

## `ollama` コマンドが見つからない

インストール直後は PATH が反映されていません。PowerShell を開き直すか:

```powershell
$env:PATH += ";$env:LOCALAPPDATA\Programs\Ollama"
```

## `python` が見つからない / Microsoft Store が開く

- https://www.python.org/downloads/ から 3.10 以上をインストール。**「Add python.exe to PATH」に必ずチェック**
- Store の別名が邪魔する場合: 設定 > アプリ > アプリ実行エイリアス で `python.exe` をオフ

## `scripts\setup.ps1` が「実行ポリシー」で止まる

`powershell -ExecutionPolicy Bypass -File scripts\setup.ps1` のように `-ExecutionPolicy Bypass` を付けて実行してください。

## 応答が遅い / 固まる

1. 初回はモデル読み込みで 30〜60 秒かかります。2回目以降を見てください
2. ブラウザ・Teams など重いアプリを閉じてメモリを空ける
3. 軽いモデルに変える: `ollama pull qwen3.5:2b` → `.env` の `LOCALAI_MODEL` を変更
4. `.env` に `LOCALAI_NUM_CTX=4096` を追加
5. タスクマネージャーでメモリが 90% 以上なら、そのPCではそのモデルは無理。1段軽いモデルへ

## 回答に `<think>` のような文字が混ざる

`localai` 経由なら自動で除去されます。`ollama` を直接呼ぶ場合は `think=False` を付けるか、`localai.strip_think()` を通してください。

## モデルのダウンロードが途中で止まる

`ollama pull <モデル名>` を再実行すると続きからダウンロードされます。
社内ネットワークでプロキシがある場合は `HTTPS_PROXY` 環境変数を設定してください。

## ポート 8501 が使用中（Streamlit）

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py --server.port 8502
```

## 文字化けする（ターミナル）

PowerShell で `chcp 65001` を実行してから再度実行するか、Windows Terminal を使ってください。

## モデルファイルの置き場所 / 削除

- 置き場所: `C:\Users\<ユーザー>\.ollama\models`
- 削除: `ollama rm <モデル名>`
- 一覧: `ollama list`
