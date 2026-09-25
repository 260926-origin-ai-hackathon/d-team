<#
.SYNOPSIS
  このPCでローカルAIが動くかの診断。RAM / GPU / Ollama / Python を確認し、おすすめモデルを表示する。
#>
Write-Host "=== ローカルAI 環境診断 ===" -ForegroundColor Cyan

$os  = Get-CimInstance Win32_OperatingSystem
$cs  = Get-CimInstance Win32_ComputerSystem
$ramGB = [math]::Round($cs.TotalPhysicalMemory / 1GB, 1)
$freeGB = [math]::Round($os.FreePhysicalMemory / 1MB, 1)
Write-Host ("OS      : " + $os.Caption)
Write-Host ("RAM     : {0} GB（空き {1} GB）" -f $ramGB, $freeGB)

$gpus = Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name
Write-Host ("GPU     : " + ($gpus -join ", "))
$hasNvidia = ($gpus -join " ") -match "NVIDIA"

$disk = Get-PSDrive -Name ($env:LOCALAPPDATA.Substring(0,1))
Write-Host ("Disk空き: {0} GB" -f [math]::Round($disk.Free / 1GB, 1))

$ollama = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollama -and (Test-Path "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe")) {
    $env:PATH += ";$env:LOCALAPPDATA\Programs\Ollama"; $ollama = Get-Command ollama
}
if ($ollama) {
    Write-Host ("Ollama  : " + (ollama --version))
    try {
        Invoke-RestMethod http://localhost:11434/api/version -TimeoutSec 3 | Out-Null
        Write-Host "          サーバー起動中"
        $tags = (Invoke-RestMethod http://localhost:11434/api/tags).models
        if ($tags) { Write-Host ("          モデル: " + (($tags | ForEach-Object { $_.name }) -join ", ")) }
        else { Write-Host "          モデル: なし（scripts\setup.ps1 を実行）" -ForegroundColor Yellow }
    } catch { Write-Host "          サーバー停止中（Ollama を起動してください）" -ForegroundColor Yellow }
} else {
    Write-Host "Ollama  : 未インストール（scripts\setup.ps1 を実行）" -ForegroundColor Yellow
}

$py = Get-Command python -ErrorAction SilentlyContinue
if ($py) { Write-Host ("Python  : " + (python --version)) } else { Write-Host "Python  : 未インストール" -ForegroundColor Yellow }

Write-Host "`n=== おすすめモデル ===" -ForegroundColor Cyan
if ($ramGB -lt 8) {
    Write-Host "RAM 8GB 未満: gemma3:1b（815MB）を推奨。 -> scripts\setup.ps1 -Model gemma3:1b"
} elseif ($ramGB -lt 16) {
    Write-Host "RAM 8-16GB : qwen3.5:2b または qwen3.5:4b（既定）。重ければ 2b へ。"
} else {
    Write-Host "RAM 16GB以上: qwen3.5:4b（既定）。余裕があれば qwen3.5:9b も可。"
}
if ($hasNvidia) { Write-Host "NVIDIA GPU あり: Ollama が自動で GPU を使います（高速）。" }
else { Write-Host "GPU なし/非NVIDIA: CPU で動作します（4b で 5-15 トークン/秒程度）。" }
