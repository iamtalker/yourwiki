param([switch]$Quiet)
$Root = Split-Path $PSScriptRoot -Parent
$targets = @((Join-Path (Split-Path $Root -Parent) 'wiki\main.amd64.exe'), (Join-Path $Root 'tools\python\python.exe'))
$p = Get-Process main.amd64, python -ErrorAction SilentlyContinue | Where-Object { $targets -contains $_.Path }
if ($p) { $p | Stop-Process -Force; if (-not $Quiet) { Write-Host '위키 서버를 껐습니다.' } }
elseif (-not $Quiet) { Write-Host '실행 중인 위키 서버가 없습니다.' }
if (-not $Quiet) { Start-Sleep -Seconds 3 }
