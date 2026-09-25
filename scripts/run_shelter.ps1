<#
.SYNOPSIS
  避難所AI を起動する（Ollama 起動確認 → モデル確認 → uvicorn 0.0.0.0:8000）。

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\run_shelter.ps1
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "先に scripts\setup.ps1 を実行してください。" -ForegroundColor Red
    exit 1
}

# Ollama が落ちていれば起動（モデルをメモリに留める設定で）
try {
    Invoke-RestMethod http://localhost:11434/api/version -TimeoutSec 3 | Out-Null
} catch {
    if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
        $env:PATH += ";$env:LOCALAPPDATA\Programs\Ollama"
    }
    $env:OLLAMA_KEEP_ALIVE = "-1"
    Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep -Seconds 4
}

# 必要なモデル（無ければ取得。ネットが無いときは事前に取っておくこと）
# 環境変数 → .env → 既定値 の順（shelter/config.py と同じ）
function EnvValue($key, $default) {
    $v = [Environment]::GetEnvironmentVariable($key)
    if ($v) { return $v }
    if (Test-Path ".env") {
        $line = Get-Content ".env" -Encoding utf8 | Where-Object { $_ -match "^\s*$key\s*=" } | Select-Object -Last 1
        if ($line) {
            $v = ($line -split "=", 2)[1].Trim()
            if ($v) { return $v }
        }
    }
    return $default
}
$chat = EnvValue "SHELTER_CHAT_MODEL" (EnvValue "LOCALAI_MODEL" "qwen3.5:4b")
$embed = EnvValue "SHELTER_EMBED_MODEL" "bge-m3"
$tags = (Invoke-RestMethod http://localhost:11434/api/tags).models.name
foreach ($m in @($chat, $embed)) {
    if ($tags -notcontains $m -and $tags -notcontains "$m`:latest") {
        Write-Host "モデル $m を取得します（初回のみ）..." -ForegroundColor Yellow
        ollama pull $m
    }
}

# 避難者の入口（ホットスポットの IP）を表示
$ip = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
       Where-Object { $_.IPAddress -like "192.168.137.*" } | Select-Object -First 1).IPAddress
Write-Host ""
Write-Host "==== 避難所AI ====" -ForegroundColor Green
Write-Host " 運営者:  http://localhost:8000/staff   （PIN は .env の SHELTER_STAFF_PIN、既定 1234）"
if ($ip) {
    Write-Host " 避難者:  http://$($ip):8000/          （モバイルホットスポットの IP）"
} else {
    Write-Host " 避難者:  モバイルホットスポットが OFF のようです（192.168.137.x が見つかりません）" -ForegroundColor Yellow
}
Write-Host " 受信をファイアウォールで許可していない場合（初回・管理者 PowerShell）:"
Write-Host '   netsh advfirewall firewall add rule name="ShelterAI 8000" dir=in action=allow protocol=TCP localport=8000'
Write-Host " （.env で SHELTER_CAPTIVE=1 にして「Wi-Fi に入るとページが開く」を試すときだけ、53/80 も許可）:"
Write-Host '   netsh advfirewall firewall add rule name="ShelterAI DNS" dir=in action=allow protocol=UDP localport=53'
Write-Host '   netsh advfirewall firewall add rule name="ShelterAI 80" dir=in action=allow protocol=TCP localport=80'
Write-Host ""

& .\.venv\Scripts\python.exe -m uvicorn shelter.main:app --host 0.0.0.0 --port 8000
