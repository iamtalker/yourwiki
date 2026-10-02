# 유어위키.exe 를 만든다(윈도우에 기본으로 들어 있는 C# 컴파일러를 쓰므로 따로 설치할 것이 없다).
# 사용: powershell -File kit\launcher\build.ps1   → 최상위 폴더에 유어위키.exe 가 생긴다.
$here = $PSScriptRoot
$top  = Split-Path (Split-Path $here -Parent) -Parent
$csc  = Get-ChildItem "$env:WINDIR\Microsoft.NET\Framework64\*\csc.exe" -ErrorAction SilentlyContinue | Sort-Object FullName | Select-Object -Last 1
if (-not $csc) { throw '윈도우의 C# 컴파일러(csc.exe)를 찾지 못했습니다(.NET Framework 4 필요).' }
$out = Join-Path $top '유어위키.exe'
& $csc.FullName /nologo /target:winexe /codepage:65001 /optimize+ /utf8output "/out:$out" "/win32icon:$(Join-Path $here 'yourwiki.ico')" `
    /reference:System.Windows.Forms.dll (Join-Path $here 'Launcher.cs')
if ($LASTEXITCODE -ne 0) { throw "빌드 실패($LASTEXITCODE)" }
"만들었습니다: $out ($((Get-Item $out).Length) 바이트)"
