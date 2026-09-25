<#
.SYNOPSIS
  ブラウザのチャット UI を起動する。
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "先に scripts\setup.ps1 を実行してください。" -ForegroundColor Red
    exit 1
}

# Ollama サーバーが落ちていれば起動
try {
    Invoke-RestMethod http://localhost:11434/api/version -TimeoutSec 3 | Out-Null
} catch {
    if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
        $env:PATH += ";$env:LOCALAPPDATA\Programs\Ollama"
    }
    Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep -Seconds 4
}

& .\.venv\Scripts\python.exe -m streamlit run app.py
