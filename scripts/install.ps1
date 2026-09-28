# 나무위키 포크 키트 설치 스크립트
# 도구 받기 → 덤프 받기(토렌트 → HTTP) → 검증 → openNAMU 초기화 → 변환
# 이미 끝난 단계는 건너뛰므로, 중간에 끊겨도 다시 실행하면 이어서 진행합니다.
param(
    [string]$Edition = '',   # 데이터 판: 2026(기본, 최신 크롤링을 나무마크로 되돌린 것) 또는 2021(공식 덤프 원문)
    [switch]$ToolsOnly,  # 도구(파이썬 등)만 받고 끝냄 — 관리판을 처음 열 때 씀
    [switch]$Seed,       # 덤프를 다 받은 뒤에도 토렌트 공유를 계속함 (창을 닫으면 멈춤)
    [switch]$Reimport,   # 이미 변환된 위키가 있어도 다시 변환함
    [switch]$NoExtras    # 보충 틀(알파위키)을 넣지 않음
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Root  = Split-Path $PSScriptRoot -Parent
$Tools = Join-Path $Root 'tools'
$Data  = Join-Path $Root 'data'
$Wiki  = Join-Path $Root 'wiki'
$cfg   = Get-Content (Join-Path $Root 'sources.json') -Raw -Encoding UTF8 | ConvertFrom-Json
New-Item -ItemType Directory -Force $Tools, $Data, $Wiki | Out-Null

function Step($msg) { Write-Host ''; Write-Host "== $msg" -ForegroundColor Cyan }

function Get-Verified($url, $out, $sha256) {
    if ((Test-Path $out) -and (Get-FileHash $out -Algorithm SHA256).Hash -eq $sha256) { return }
    Write-Host "  받는 중: $url"
    Invoke-WebRequest $url -OutFile $out -UseBasicParsing
    $h = (Get-FileHash $out -Algorithm SHA256).Hash
    if ($h -ne $sha256) { Remove-Item $out; throw "해시가 맞지 않습니다: $out`n  기대 $sha256`n  실제 $h" }
}

# ---------------------------------------------------------------- 1. 도구
Step '1/6 도구 준비 (파이썬, 7zr, aria2, openNAMU)'
$t = $cfg.tools
$pyZip = Join-Path $Tools 'python.zip'
Get-Verified $t.python.url $pyZip $t.python.sha256
$python = Join-Path $Tools 'python\python.exe'
if (-not (Test-Path $python)) { Expand-Archive $pyZip (Join-Path $Tools 'python') -Force }

$sevenZip = Join-Path $Tools '7zr.exe'
Get-Verified $t.'7zr'.url $sevenZip $t.'7zr'.sha256

$ariaZip = Join-Path $Tools 'aria2.zip'
Get-Verified $t.aria2.url $ariaZip $t.aria2.sha256
$aria = Get-ChildItem (Join-Path $Tools 'aria2') -Recurse -Filter aria2c.exe -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $aria) {
    Expand-Archive $ariaZip (Join-Path $Tools 'aria2') -Force
    $aria = Get-ChildItem (Join-Path $Tools 'aria2') -Recurse -Filter aria2c.exe | Select-Object -First 1
}
$aria = $aria.FullName

$exe = Join-Path $Wiki 'main.amd64.exe'
Get-Verified $t.opennamu.url $exe $t.opennamu.sha256
Write-Host '  완료'
if ($ToolsOnly) { exit 0 }

# ---------------------------------------------------------------- 2. 덤프
if (-not $Edition) { $Edition = $cfg.default_edition }
$d = $cfg.editions.$Edition
if (-not $d) { throw "알 수 없는 판입니다: $Edition (sources.json 의 editions 참고)" }
$dump = Join-Path $Data $d.file
function Test-Dump {
    (Test-Path $dump) -and (Get-Item $dump).Length -eq $d.size -and
    $(if ($d.sha256) { (Get-FileHash $dump -Algorithm SHA256).Hash -eq $d.sha256.ToUpper() } else { (Get-FileHash $dump -Algorithm MD5).Hash -eq $d.md5.ToUpper() })
}
Step "2/6 나무위키 덤프 받기 ($($d.date)판, 약 $([math]::Round($d.size/1GB,1))GB)"
if (Test-Dump) {
    Write-Host '  이미 받아 두었습니다.'
} else {
    foreach ($s in $d.sources) {
        Write-Host "  경로: $($s.type) $($s.url)"
        try {
            if ($s.type -eq 'torrent') {
                $tor = Join-Path $Tools 'dump.torrent'
                Invoke-WebRequest $s.url -OutFile $tor -UseBasicParsing
                $tmp = Join-Path $Data 'torrent'
                $opts = @("--select-file=$($s.select_file)", "--dir=$tmp", '--bt-stop-timeout=180',
                          '--file-allocation=none', '--summary-interval=30', '--console-log-level=warn')
                if (-not $Seed) { $opts += '--seed-time=0' }
                & $aria $tor @opts
                $got = Get-ChildItem $tmp -Recurse -Filter $d.file | Select-Object -First 1
                if ($got -and $got.Length -eq $d.size) { Move-Item $got.FullName $dump -Force }
                Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
            } else {
                & $aria $s.url "--dir=$Data" "--out=$($d.file)" -x4 -c `
                    --file-allocation=none --summary-interval=30 --console-log-level=warn
            }
        } catch { Write-Host "  실패: $_" -ForegroundColor Yellow }
        if (Test-Dump) { break }
        Write-Host '  이 경로로는 받지 못했습니다. 다음 경로를 시도합니다.' -ForegroundColor Yellow
    }
    if (-not (Test-Dump)) { throw '덤프를 받지 못했습니다. 인터넷 연결을 확인하고 다시 실행하세요.' }
}
Write-Host '  검증 완료 (해시 일치)'

# ---------------------------------------------------------------- 3. openNAMU 초기화
Step '3/6 위키 엔진 초기화'
$Port = 3001   # openNAMU 내부 포트 (시작.bat 은 중계 서버를 3000 에 띄움)
function Wait-Server($sec) {
    foreach ($i in 1..$sec) {
        if ($(try { $c = New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1', $Port); $c.Close(); $true } catch { $false })) { return $true }
        Start-Sleep -Seconds 1
    }
    return $false
}
function Start-Engine { Start-Process $exe -ArgumentList "$Port", '--localhost' -WorkingDirectory $Wiki -WindowStyle Hidden -PassThru }
if (-not (Test-Path (Join-Path $Wiki 'data.db'))) {
    $p = Start-Engine
    $ok = Wait-Server 120
    Stop-Process -Id $p.Id -Force
    Start-Sleep -Seconds 1
    if (-not $ok) { throw "openNAMU 가 시작되지 않았습니다. $Port 번 포트를 다른 프로그램이 쓰고 있는지 확인하세요." }
}
Write-Host '  완료'

# ---------------------------------------------------------------- 4. 변환
Step '4/6 문서 변환 (수 분 걸립니다)'
$env:PYTHONUTF8 = '1'
$count = & $python -c "import sqlite3,sys; print(sqlite3.connect(sys.argv[1]).execute('select count(*) from data').fetchone()[0])" (Join-Path $Wiki 'data.db')
if ([int]$count -gt 0 -and -not $Reimport) {
    Write-Host "  이미 $count 개 문서가 있습니다. 다시 변환하려면 -Reimport 로 실행하세요."
} else {
    & $python (Join-Path $PSScriptRoot 'import_dump.py') $dump $Wiki --7z $sevenZip --dump-date $d.date
    if ($LASTEXITCODE -ne 0) { throw '변환에 실패했습니다.' }
}

# ---------------------------------------------------------------- 4-2. 보충 틀
if (-not $NoExtras) {
    foreach ($x in Get-ChildItem (Join-Path $Root 'extras') -Filter '*.jsonl.gz' -ErrorAction SilentlyContinue) {
        Write-Host "  보충 틀: $($x.Name)"
        & $python (Join-Path $PSScriptRoot 'import_templates.py') $x.FullName $Wiki
    }
}

# ---------------------------------------------------------------- 5. 검색 색인
# openNAMU 는 시작할 때 색인이 없으면 만든다. 중간에 끄면 처음부터 다시 만들므로 설치 때 끝낸다.
Step '5/6 검색 색인 만들기 (문서가 많아 수십 분 걸릴 수 있습니다. 창을 닫지 마세요)'
$ver = Join-Path $Wiki 'data\bleve.version'
if (Test-Path $ver) {
    Write-Host '  이미 만들어져 있습니다.'
} else {
    $p = Start-Engine
    $t0 = Get-Date
    while (-not (Test-Path $ver)) {
        if ($p.HasExited) { throw '색인을 만드는 중 위키 엔진이 멈췄습니다. 설치.bat 을 다시 실행하세요.' }
        Start-Sleep -Seconds 30
        Write-Host ("  진행 중... {0:N0}분 경과" -f ((Get-Date) - $t0).TotalMinutes)
    }
    Stop-Process -Id $p.Id -Force
    Write-Host '  완료'
}

# ---------------------------------------------------------------- 6. 끝
Step '6/6 설치 완료'
Write-Host '  시작.bat 을 더블클릭하면 위키가 열립니다.'
Write-Host '  ※ 이 데이터는 CC BY-NC-SA 2.0 KR입니다. 상업적 이용은 금지됩니다. 이 키트를 사용해 광고를 붙이거나 상업적으로 운영하는 것은 라이선스 위반입니다.' -ForegroundColor Yellow
