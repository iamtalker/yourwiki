"""유어위키 관리판: 설치·켜기·끄기·동기화를 브라우저 화면 하나에서 한다 (파이썬 표준 라이브러리만 사용).

    python panel.py            # http://127.0.0.1:3100 에 관리판을 열고 브라우저를 띄운다

관리판이 위키 엔진(openNAMU, 3001), 중계 서버(offline_proxy, 3000), 갱신기(updater)를 띄우고 끈다.
관리판 창을 닫으면 함께 띄운 프로그램들도 꺼진다.
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPTS)
WIKI = os.path.join(ROOT, "wiki")
PANEL_PORT = 3100
SETTINGS = os.path.join(ROOT, "panel.json")
WIN = os.name == "nt"
ENGINE = os.path.join(WIKI, "main.amd64.exe" if WIN else "main.bin")
NO_WINDOW = 0x08000000 if WIN else 0  # CREATE_NO_WINDOW

procs = {}
lock = threading.Lock()
_cache = {}


def settings():
    try:
        return json.load(open(SETTINGS, encoding="utf-8"))
    except Exception:
        return {"sync": "auto", "listen": "127.0.0.1:3000"}


def save_settings(s):
    json.dump(s, open(SETTINGS, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def alive(name):
    p = procs.get(name)
    return p is not None and p.poll() is None


def spawn(name, args, log, cwd=None):
    out = open(os.path.join(ROOT, log), "a", encoding="utf-8", errors="replace")
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    procs[name] = subprocess.Popen(args, cwd=cwd or ROOT, stdout=out, stderr=subprocess.STDOUT,
                                   env=env, creationflags=NO_WINDOW)


def stop(name):
    p = procs.pop(name, None)
    if p and p.poll() is None:
        p.terminate()
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()


def start_updater():
    stop("updater")
    mode = settings()["sync"]
    if mode == "off" or not os.path.exists(os.path.join(WIKI, "data.db")):
        return
    args = [sys.executable, os.path.join(SCRIPTS, "updater.py"), WIKI, "--watch"]
    if mode == "queue":
        args.append("--queue-only")
    spawn("updater", args, "updater.log")


def start_wiki():
    with lock:
        if not os.path.exists(os.path.join(WIKI, "data.db")):
            return "아직 설치되지 않았습니다"
        if not alive("engine"):
            spawn("engine", [ENGINE, "3001", "--localhost"], "server.log", cwd=WIKI)
        if not alive("proxy"):
            spawn("proxy", [sys.executable, os.path.join(SCRIPTS, "offline_proxy.py"), os.path.join(ROOT, "assets"),
                            "--listen", settings()["listen"], "--upstream", "127.0.0.1:3001",
                            "--queue-db", os.path.join(WIKI, "updater.db")], "proxy.log")
        if not alive("updater"):
            start_updater()

    def open_when_ready():  # 엔진이 준비되면 첫 화면을 브라우저로 연다
        for _ in range(600):
            if port_open(3001):
                webbrowser.open("http://" + settings()["listen"].replace("0.0.0.0", "127.0.0.1") + "/")
                return
            time.sleep(1)
    threading.Thread(target=open_when_ready, daemon=True).start()
    return "켰습니다. 준비되면 첫 화면이 자동으로 열립니다"


def stop_wiki():
    with lock:
        for n in ("tunnel", "updater", "proxy", "engine"):
            stop(n)
    return "껐습니다"


def run_install(edition):
    if alive("install"):
        return "이미 설치 중입니다"
    if not WIN:
        return "리눅스는 README 의 '리눅스 서버에 직접 설치하기'를 따라 주세요"
    stop_wiki()
    open(os.path.join(ROOT, "install.log"), "w").close()
    spawn("install", ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                      os.path.join(SCRIPTS, "install.ps1"), "-Edition", edition], "install.log")
    return "설치를 시작했습니다"


def ensure_cloudflared():
    """터널 프로그램(cloudflared)을 공식 배포처에서 받아 SHA-256 으로 검증한다."""
    import hashlib
    import urllib.request
    exe = os.path.join(ROOT, "tools", "cloudflared.exe")
    t = json.load(open(os.path.join(ROOT, "sources.json"), encoding="utf-8"))["tools"]["cloudflared"]
    def ok():
        return os.path.exists(exe) and hashlib.sha256(open(exe, "rb").read()).hexdigest() == t["sha256"]
    if not ok():
        urllib.request.urlretrieve(t["url"], exe)
        if not ok():
            os.remove(exe)
            raise RuntimeError("터널 프로그램 해시가 맞지 않습니다")
    return exe


def tunnel(on):
    if not on:
        stop("tunnel")
        _cache.pop("url", None)
        return "공개를 껐습니다"
    if not WIN:
        return "리눅스 서버는 server/yourwiki.sh 를 쓰세요"
    if not alive("proxy"):
        return "먼저 위키를 켜세요"
    if alive("tunnel"):
        return "이미 공개 중입니다"
    try:
        exe = ensure_cloudflared()
    except Exception as e:
        return f"터널 프로그램을 받지 못했습니다: {e}"
    open(os.path.join(ROOT, "tunnel.log"), "w").close()
    spawn("tunnel", [exe, "tunnel", "--no-autoupdate", "--url", "http://" + settings()["listen"]], "tunnel.log")
    return "공개를 시작했습니다. 잠시 뒤 공개 주소가 표시됩니다"


def tunnel_url():
    import re
    if not alive("tunnel"):
        return ""
    for line in tail("tunnel.log", 200):
        m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
        if m:
            return m.group(0)
    return ""


def tail(name, n=12):
    try:
        with open(os.path.join(ROOT, name), encoding="utf-8", errors="replace") as f:
            return f.readlines()[-n:]
    except OSError:
        return []


def port_open(port):
    import socket
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def status():
    s = settings()
    st = {"installed": os.path.exists(os.path.join(WIKI, "data.db")), "sync": s["sync"], "listen": s["listen"],
          "running": {n: alive(n) for n in ("engine", "proxy", "updater", "install")}}
    st["ready"] = st["running"]["engine"] and port_open(3001)
    # 위키 엔진이 켜져 있을 때는 data.db 를 열지 않는다.
    # (엔진이 시작할 때 DB 방식을 바꾸는 동안 다른 연결이 있으면 계속 기다리며 켜지지 않는다)
    if st["installed"] and not st["running"]["engine"] and not st["running"]["install"]:
        try:
            db = sqlite3.connect(f"file:{os.path.join(WIKI, 'data.db')}?mode=ro", uri=True, timeout=5)
            r = db.execute("select data from other where name = 'count_all_title'").fetchone()
            _cache["docs"] = int(r[0]) if r else None
            db.close()
        except Exception:
            pass
    st["docs"] = _cache.get("docs")
    try:
        q = sqlite3.connect(os.path.join(WIKI, "updater.db"), timeout=5)
        st["queue"] = q.execute("select count(*) from queue").fetchone()[0]
        st["fetched_today"] = q.execute("select count(*) from fetched where at > ?", (time.time() - 86400,)).fetchone()[0]
        q.close()
    except Exception:
        st["queue"] = st["fetched_today"] = 0
    du = shutil.disk_usage(ROOT)
    st["disk_free_gb"] = round(du.free / 1e9, 1)
    st["install_log"] = tail("install.log")
    st["updater_log"] = tail("updater.log", 8)
    st["running"]["tunnel"] = alive("tunnel")
    st["public_url"] = tunnel_url()
    return st


PAGE = r"""<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>유어위키 관리판</title><style>
body{font-family:system-ui,sans-serif;max-width:760px;margin:24px auto;padding:0 16px;color:#222}
h1{font-size:22px}section{border:1px solid #ddd;border-radius:8px;padding:14px 16px;margin:12px 0}
h2{font-size:16px;margin:0 0 8px}button{font-size:14px;padding:6px 12px;margin:2px;cursor:pointer}
.on{color:#0a0}.off{color:#999}pre{background:#f6f6f6;padding:8px;font-size:12px;white-space:pre-wrap;max-height:220px;overflow:auto}
.warn{font-size:12px;color:#a60}label{margin-right:12px}
</style><h1>유어위키 관리판</h1>
<p class="warn">이 데이터는 CC BY-NC-SA 2.0 KR입니다. 상업적 이용은 금지됩니다. 이 키트를 사용해 광고를 붙이거나 상업적으로 운영하는 것은 라이선스 위반입니다.</p>
<section><h2>상태</h2><div id="st">불러오는 중…</div></section>
<section><h2>위키</h2>
<button onclick="act('start')">켜기</button><button onclick="act('stop')">끄기</button>
<button onclick="openWiki()">위키 열기</button></section>
<section><h2>나무위키 최신판 동기화</h2>
<label><input type="radio" name="sync" value="off" onchange="setSync(this.value)"> 끄기</label>
<label><input type="radio" name="sync" value="queue" onchange="setSync(this.value)"> 요청한 문서만</label>
<label><input type="radio" name="sync" value="auto" onchange="setSync(this.value)"> 자동 따라잡기 (기본)</label>
<p style="font-size:13px;color:#555">robots.txt 준수 · 6초에 1건 이하 · 캡차·차단 감지 시 즉시 중단 · 같은 문서는 24시간에 한 번.
문서 화면의 「🔄 나무위키 최신판으로 갱신」 단추로 요청할 수 있습니다.</p>
<div id="sync"></div><pre id="ulog"></pre></section>
<section><h2>인터넷에 공개</h2>
<button onclick="pub(1)">공개하기</button><button onclick="act2('tunnel?on=0')">공개 끄기</button>
<div id="pubinfo" style="margin:6px 0;font-weight:bold"></div>
<p style="font-size:13px;color:#555">공유기 설정 없이 Cloudflare 임시 공개 주소(https)를 만듭니다. 켤 때마다 주소가 바뀝니다.<br>
<b>공개 전에</b>: 위키에서 먼저 가입해 관리자가 되고, 관리자 설정 → 권한에서 비로그인(ip) 사용자의 편집을 막으세요.
공개하는 순간 그 사이트의 운영 책임(권리 침해·게시중단 요청 대응 등)은 공개한 사람에게 있습니다.</p></section>
<section><h2>설치 · 데이터</h2>
<button onclick="install('2026')">설치 / 다시 설치</button>
<p style="font-size:13px;color:#555">다시 설치하면 이미 받은 파일은 건너뜁니다. 위키는 설치 동안 꺼집니다.</p><pre id="ilog"></pre></section>
<script>
let listen="127.0.0.1:3000";
async function api(p){const r=await fetch(p,{method:'POST'});return (await r.json()).msg}
async function act(a){alert(await api('/api/'+a));load()}
async function act2(p){alert(await api('/api/'+p));load()}
async function pub(){if(confirm('위키를 인터넷에 공개할까요? 누구나 주소로 접속할 수 있게 됩니다.')){act2('tunnel?on=1')}}
async function install(e){if(confirm('설치할까요? 수십 분 이상 걸릴 수 있습니다.')){alert(await api('/api/install?edition='+e));load()}}
async function setSync(m){await api('/api/sync?mode='+m);load()}
function openWiki(){window.open('http://'+listen.replace('0.0.0.0','127.0.0.1')+'/','_blank')}
function dot(b){return b?'<span class=on>●</span>':'<span class=off>○</span>'}
async function load(){const s=await (await fetch('/api/status')).json();listen=s.listen;
document.getElementById('st').innerHTML=(s.installed?'설치됨 · 문서 '+(s.docs??'?').toLocaleString()+'개':'아직 설치되지 않음')+
' · 디스크 여유 '+s.disk_free_gb+'GB<br>'+dot(s.running.engine)+' 위키 엔진 '+dot(s.running.proxy)+' 중계 서버 '+
dot(s.running.updater)+' 갱신기 '+(s.running.install?'· <b>설치 진행 중</b>':'')+
(s.running.engine?(s.ready?'<br><b class=on>위키 준비됨 — [위키 열기]를 누르세요</b>':'<br><b>위키 엔진 시작 중… (문서가 많아 몇 분 걸릴 수 있습니다)</b>'):'');
document.querySelectorAll('input[name=sync]').forEach(x=>x.checked=x.value==s.sync);
document.getElementById('sync').textContent='대기열 '+s.queue+'개 · 최근 24시간 받은 문서 '+s.fetched_today+'개';
document.getElementById('ulog').textContent=s.updater_log.join('');
document.getElementById('ilog').textContent=s.install_log.join('');
document.getElementById('pubinfo').innerHTML=s.public_url?('공개 주소: <a href="'+s.public_url+'" target=_blank>'+s.public_url+'</a>'):(s.running.tunnel?'공개 주소를 만드는 중…':'')}
load();setInterval(load,3000);
</script></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype):
        b = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/":
            return self.send(200, PAGE, "text/html; charset=utf-8")
        if path == "/api/status":
            return self.send(200, json.dumps(status(), ensure_ascii=False), "application/json")
        self.send(404, "없음", "text/plain; charset=utf-8")

    def do_POST(self):
        u = urlsplit(self.path)
        q = parse_qs(u.query)
        if u.path == "/api/start":
            msg = start_wiki()
        elif u.path == "/api/stop":
            msg = stop_wiki()
        elif u.path == "/api/tunnel":
            msg = tunnel((q.get("on") or ["1"])[0] == "1")
        elif u.path == "/api/install":
            msg = run_install((q.get("edition") or ["2026"])[0])
        elif u.path == "/api/sync":
            s = settings()
            s["sync"] = (q.get("mode") or ["queue"])[0]
            save_settings(s)
            if alive("engine"):
                start_updater()
            msg = "동기화 설정을 바꿨습니다"
        else:
            return self.send(404, "{}", "application/json")
        self.send(200, json.dumps({"msg": msg}, ensure_ascii=False), "application/json")


def cleanup_leftovers():
    """전에 관리판 창을 그냥 닫아서 남은 위키 프로그램(엔진·중계 서버·갱신기·터널)을 정리한다."""
    if not WIN:
        return
    ps = ("$root = '" + ROOT.replace("'", "''") + "'; "
          "Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne " + str(os.getpid()) + " -and "
          "$_.ExecutablePath -and $_.ExecutablePath.StartsWith($root) -and "
          "$_.Name -in @('main.amd64.exe','python.exe','cloudflared.exe') } | "
          "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], creationflags=NO_WINDOW,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    cleanup_leftovers()
    srv = ThreadingHTTPServer(("127.0.0.1", PANEL_PORT), Handler)
    url = f"http://127.0.0.1:{PANEL_PORT}/"
    print(f"유어위키 관리판: {url}  (끌 때는 관리판에서 [끄기]를 누르세요)", flush=True)
    webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_wiki()


if __name__ == "__main__":
    main()
