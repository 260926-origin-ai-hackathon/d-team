<#
.SYNOPSIS
  初回セットアップ（Windows）。Ollama のインストール → モデル取得 → Python 環境構築。

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -Model gemma3:1b
  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -ShelterModels @()   # 避難所AI 用を取らない
#>
param(
    [string]$Model = "qwen3.5:4b",
    # 避難所AI（shelter/）が使うモデル: チャット（.env の SHELTER_CHAT_MODEL）と埋め込み（SHELTER_EMBED_MODEL）
    [string[]]$ShelterModels = @("qwen3:1.7b", "bge-m3"),
    [switch]$SkipModel
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

# ---- 1. Ollama ----------------------------------------------------------------
Step "Ollama の確認"
$ollamaExe = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
if (-not (Get-Command ollama -ErrorAction SilentlyContinue) -and -not (Test-Path $ollamaExe)) {
    Write-Host "Ollama が見つかりません。winget でインストールします..."
    winget install --id Ollama.Ollama -e --silent --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) {
        Write-Host "winget でのインストールに失敗しました。https://ollama.com/download から手動でインストールしてください。" -ForegroundColor Red
        exit 1
    }
}
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    $env:PATH += ";$env:LOCALAPPDATA\Programs\Ollama"
}
Write-Host ("ollama: " + (ollama --version))

# ---- 2. Ollama サーバー起動 ---------------------------------------------------
Step "Ollama サーバーの起動確認"
try {
    Invoke-RestMethod http://localhost:11434/api/version -TimeoutSec 3 | Out-Null
    Write-Host "起動済み"
} catch {
    Write-Host "起動していないので起動します..."
    Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep -Seconds 4
}

# ---- 3. モデル取得 --------------------------------------------------------------
if (-not $SkipModel) {
    Step "モデル $Model の取得（初回は数GB。数分かかります）"
    ollama pull $Model
    foreach ($m in $ShelterModels) {
        Step "避難所AI 用モデル $m の取得"
        ollama pull $m
    }
}

# ---- 4. Python 仮想環境 ---------------------------------------------------------
Step "Python 仮想環境の作成"
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command py -ErrorAction SilentlyContinue }
if (-not $py) {
    Write-Host "Python が見つかりません。https://www.python.org/downloads/ から 3.10 以上をインストールしてください（'Add python.exe to PATH' にチェック）。" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path ".venv")) { & $py.Source -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip -q
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt -q

# ---- 5. .env -------------------------------------------------------------------
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    (Get-Content ".env" -Encoding utf8) -replace "^LOCALAI_MODEL=.*", "LOCALAI_MODEL=$Model" | Set-Content ".env" -Encoding utf8
}

Step "完了"
Write-Host @"

次のコマンドで動かせます:

  ブラウザUI:   powershell -ExecutionPolicy Bypass -File scripts\run_ui.ps1
  ターミナル:   .\.venv\Scripts\python.exe -m localai.chat_cli
  動作確認:     .\.venv\Scripts\python.exe scripts\smoke_test.py

"@
