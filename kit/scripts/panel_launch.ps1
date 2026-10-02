# 유어위키 관리판 실행: 파이썬이 없으면 도구만 먼저 받는다
$Root = Split-Path $PSScriptRoot -Parent
$py = Join-Path $Root 'tools\python\python.exe'
if (-not (Test-Path $py)) {
    Write-Host '처음 실행: 필요한 도구를 받습니다(약 100MB)...'
    & (Join-Path $PSScriptRoot 'install.ps1') -ToolsOnly
}
$env:PYTHONUTF8 = '1'
& $py (Join-Path $PSScriptRoot 'panel.py')