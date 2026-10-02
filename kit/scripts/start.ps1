param([string]$Listen = '127.0.0.1:3000')   # 인터넷 공개: -Listen 0.0.0.0:3000
$Root   = Split-Path $PSScriptRoot -Parent
$Wiki   = Join-Path (Split-Path $Root -Parent) 'wiki'   # 위키는 키트(kit) 폴더 밖, 유어위키.exe 옆
$exe    = Join-Path $Wiki 'main.amd64.exe'
$python = Join-Path $Root 'tools\python\python.exe'
if (-not (Test-Path (Join-Path $Wiki 'data.db'))) {
    Write-Host '아직 설치되지 않았습니다. 유어위키.exe 를 열어 [설치]를 먼저 누르세요.'
    Read-Host '엔터를 누르면 닫힙니다'
    exit 1
}
& (Join-Path $PSScriptRoot 'stop.ps1') -Quiet   # 전에 남은 서버가 있으면 정리

Write-Host '위키 서버를 켭니다. 이 창을 닫으면 서버가 꺼집니다.'
Write-Host '준비되면 브라우저가 자동으로 열립니다.'
Write-Host '  ※ 이 데이터는 CC BY-NC-SA 2.0 KR입니다. 상업적 이용은 금지됩니다. 이 키트를 사용해 광고를 붙이거나 상업적으로 운영하는 것은 라이선스 위반입니다.' -ForegroundColor Yellow
# openNAMU 는 내부 포트(3001)에서, 중계 서버(offline_proxy)는 바깥 포트에서 받는다.
# 같은 창(-NoNewWindow)에서 띄워야 창을 닫을 때 함께 꺼진다.
Start-Process $exe -ArgumentList '3001', '--localhost' -WorkingDirectory $Wiki -NoNewWindow `
    -RedirectStandardOutput (Join-Path $Wiki 'server.log') -RedirectStandardError (Join-Path $Wiki 'server.err.log')
Start-Job -ArgumentList $Listen {
    param($listen)
    $port = [int]($listen.Split(':')[-1])
    foreach ($i in 1..600) {
        try { $c = New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1', 3001); $c.Close(); break }
        catch { Start-Sleep -Seconds 1 }
    }
    Start-Process "http://127.0.0.1:$port/"
} | Out-Null
$env:PYTHONUTF8 = '1'
& $python (Join-Path $PSScriptRoot 'offline_proxy.py') (Join-Path $Root 'assets') --listen $Listen --upstream 127.0.0.1:3001
