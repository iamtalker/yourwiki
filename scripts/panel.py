"""유어위키 관리판: 설치·켜기·끄기·동기화를 브라우저 화면 하나에서 한다 (파이썬 표준 라이브러리만 사용).

    python panel.py            # http://127.0.0.1:3100 에 관리판을 열고 브라우저를 띄운다

관리판이 위키 엔진(openNAMU, 3001), 중계 서버(offline_proxy, 3000), 갱신기(updater)를 띄우고 끈다.
관리판 창을 닫으면 함께 띄운 프로그램들도 꺼진다.
"""
import json
import re
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # 임베디드 파이썬은 스크립트 폴더를 path에 넣지 않음

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
_bye = {"t": 0.0}  # 브라우저 창이 닫혔다는 신호를 받은 시각(0 이면 없음)
_cache = {}


DEFAULTS = {"sync": "auto", "listen": "127.0.0.1:3000", "color": "#3b5bdb"}


def settings():
    """관리판에서 고른 설정(panel.json). 관리판을 껐다 켜도 그대로 남는다.
    wiki_on·public 은 '켜 둔 상태'도 기억해 두었다가 다음에 관리판을 열면 다시 켠다."""
    for path in (SETTINGS, SETTINGS + ".bak"):  # 본 파일이 깨졌으면 직전 판으로
        try:
            s = json.load(open(path, encoding="utf-8"))
            if isinstance(s, dict):
                return dict(DEFAULTS, **s)
        except Exception:
            pass
    return dict(DEFAULTS)


def save_settings(s):
    """저장 도중 꺼져도 설정이 날아가지 않게: 임시 파일에 다 쓴 뒤 바꿔 끼우고, 직전 판은 .bak 으로 둔다."""
    tmp = SETTINGS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    if os.path.exists(SETTINGS):
        try:
            shutil.copyfile(SETTINGS, SETTINGS + ".bak")
        except OSError:
            pass
    os.replace(tmp, SETTINGS)


def remember(**kv):
    s = settings()
    s.update(kv)
    save_settings(s)


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
    if settings().get("p2p"):
        args.append("--p2p")
    spawn("updater", args, "updater.log")


def start_p2p():
    """P2P: 내 창구(읽기 전용, 127.0.0.1:3002) + 그 창구만 여는 임시 공개 주소 + 작업자.

    위키 자체를 공개하는 [공개하기]와는 별개다. P2P 창구로는 서명된 문서 묶음만 나가고 위키 화면은 나가지 않는다.
    """
    for n in ("p2p", "p2ptunnel", "p2pwin"):
        stop(n)
    s = settings()
    if not s.get("p2p") or not os.path.exists(os.path.join(WIKI, "data.db")):
        return
    mode = s.get("p2p_open", "tunnel")  # tunnel: Cloudflare 임시 주소 · direct: 공유기 포트·IPv6 · both: 둘 다
    direct = mode in ("direct", "both")
    # 직접 연결이면 창구를 바깥에서도 받게 연다(읽기 전용: 서명된 문서 묶음만 나감)
    listen = ("[::]:" if direct else "127.0.0.1:") + str(P2P_PORT)
    spawn("p2pwin", [sys.executable, os.path.join(SCRIPTS, "hub_server.py"), "--read-only",
                     "--listen", listen, "--db", os.path.join(WIKI, "hub.db")], "p2p-window.log")
    args = [sys.executable, os.path.join(SCRIPTS, "p2p.py"), WIKI, "--watch", "--window-port", str(P2P_PORT)]
    if direct:
        args.append("--direct")
    if s.get("p2p_url"):  # 고정 주소(내 도메인 등)가 있으면 임시 주소를 만들지 않는다
        args += ["--self-url", s["p2p_url"]]
    elif mode == "direct":
        pass
    else:
        try:
            import cloudflared
            exe = cloudflared.ensure()
            open(os.path.join(ROOT, "p2p-tunnel.log"), "w").close()
            spawn("p2ptunnel", [exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{P2P_PORT}"],
                  "p2p-tunnel.log")
            args += ["--tunnel-log", os.path.join(ROOT, "p2p-tunnel.log")]
        except Exception as e:  # 터널이 없으면 받기만 한다
            with open(os.path.join(ROOT, "p2p.log"), "a", encoding="utf-8") as f:
                f.write(f"P2P 창구 공개 주소를 만들지 못했습니다(받기만 합니다): {e}\n")
    for h in p2p_hubs():
        args += ["--hub", h]
    for f in s.get("p2p_friends", []):
        args += ["--friend", f]
    spawn("p2p", args, "p2p.log")


def p2p_hubs():
    """관리판에 적은 중계소, 없으면 sources.json 의 기본 중계소."""
    import p2p
    return settings().get("p2p_hubs") or p2p.default_hubs()


def start_proxy():
    stop("proxy")
    args = [sys.executable, os.path.join(SCRIPTS, "offline_proxy.py"), os.path.join(ROOT, "assets"),
            "--listen", settings()["listen"], "--upstream", "127.0.0.1:3001",
            "--queue-db", os.path.join(WIKI, "updater.db")]
    spawn("proxy", args, "proxy.log")


def port_owner(port):
    """그 포트를 듣고 있는 프로세스의 (PID, 실행 파일 경로, 명령줄). 비어 있으면 None. Windows 만(그 밖은 None)."""
    if not WIN:
        return None
    ps = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
          f"$c = Get-NetTCPConnection -LocalPort {int(port)} -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1; "
          "if ($c) { $p = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $c.OwningProcess); "
          "Write-Output ([string]$c.OwningProcess + '|' + [string]$p.ExecutablePath + '|' + [string]$p.CommandLine) }")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, creationflags=NO_WINDOW, timeout=30)
        out = r.stdout.decode("utf-8", "replace").strip()
    except (OSError, subprocess.SubprocessError):
        return None
    if out.count("|") < 2:
        return None
    pid, path, cmd = out.split("|", 2)
    return int(pid), path.strip(), cmd.strip()


KIT_SCRIPT = re.compile(r"([A-Za-z]:[\\/][^\"]*?)[\\/]scripts[\\/](?:offline_proxy|updater|p2p|hub_server|panel)\.py", re.I)


def other_kit_root(owner):
    """포트 주인이 '다른 폴더의 유어위키 프로그램'으로 확인되면 그 폴더(루트)를, 아니면 None.
    확인 방법: 실행 파일에서 위로 올라가며 scripts/panel.py 와 sources.json 이 있는 폴더를 찾거나, 명령줄이 이 키트의 스크립트를 가리킴."""
    pid, exe, cmd = owner
    d = os.path.dirname(exe or "")
    for _ in range(4):
        if d and os.path.exists(os.path.join(d, "scripts", "panel.py")) and os.path.exists(os.path.join(d, "sources.json")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    m = KIT_SCRIPT.search(cmd or "")
    if m and os.path.exists(os.path.join(m.group(1), "scripts", "panel.py")):
        return m.group(1)
    return None


def same_dir(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def kit_busy(root):
    """그 폴더의 유어위키가 설치·가져오기·내보내기 같은 작업 중인가(Windows). 작업 중이면 그 엔진을 끄면 작업이 망가진다."""
    if not WIN:
        return False
    ps = ("$root = '" + os.path.abspath(root).replace("'", "''") + "'; "
          r"$pat = [regex]::Escape($root + '\scripts\') + '(install\.ps1|import_dump\.py|wiki_pack\.py|export_dump\.py|convert_wiki\.py|build_2026\.py)'; "
          r"@(Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(powershell|pwsh|python|pythonw)\.exe$' -and "
          r"$_.CommandLine -and $_.ProcessId -ne $PID -and $_.CommandLine -match $pat }).Count")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, creationflags=NO_WINDOW, timeout=30)
        return int((r.stdout.decode("utf-8", "replace").strip() or "0")) > 0
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def free_ports_from_other_kits(ports):
    """다른 폴더의 유어위키가 이 포트들을 쓰고 있으면 그 위키(관리판 포함)를 끄고 포트가 비기를 기다린다.
    유어위키 프로그램으로 확인되지 않는 프로그램은 건드리지 않고, 그 폴더가 설치·가져오기 중이면 끄지 않는다."""
    killed = set()
    for port in ports:
        owner = port_owner(port)
        if not owner:
            continue
        root = other_kit_root(owner)
        if root and not same_dir(root, ROOT) and kit_busy(root):
            print(f"다른 폴더의 유어위키({root})가 설치·가져오기 같은 작업 중이라 끄지 않습니다", flush=True)
            continue
        if root and not same_dir(root, ROOT) and os.path.normcase(os.path.abspath(root)) not in killed:
            print(f"다른 폴더의 유어위키({root})가 포트 {port} 을(를) 쓰고 있어 먼저 끕니다", flush=True)
            sweep_wiki_processes(root, include_panel=True)
            killed.add(os.path.normcase(os.path.abspath(root)))
    if killed:
        for _ in range(20):  # 최대 10초 기다린다
            if not any(port_open(p) for p in ports):
                break
            time.sleep(0.5)


def foreign_port_conflict(ports):
    """우리 폴더가 아닌 곳의 프로그램이 이 포트들을 쓰고 있으면 안내 글을, 아니면 빈 글을 돌려준다.
    다른 폴더에서 켠 유어위키가 꺼지지 않은 채 있으면 새 폴더에서 켜도 옛 위키가 보이기 때문이다."""
    root = os.path.normcase(os.path.abspath(ROOT)) + os.sep
    for port in ports:
        owner = port_owner(port)
        if owner and not os.path.normcase(os.path.abspath(owner[1] or "")).startswith(root):
            where = owner[1] or f"PID {owner[0]}"
            return (f"포트 {port} 을(를) 이 폴더가 아닌 프로그램이 쓰고 있습니다: {where}\n"
                    "다른 폴더에서 켠 유어위키가 꺼지지 않았다면 그쪽 관리판에서 [끄기]를 누르거나 작업 관리자에서 끈 뒤 다시 켜세요. "
                    "(그대로 두면 이 폴더가 아니라 그 위키가 보입니다)")
    return ""


def start_wiki(open_browser=True):
    with lock:
        if not os.path.exists(os.path.join(WIKI, "data.db")):
            return "아직 설치되지 않았습니다"
        if alive("import"):
            return "가져오는 중입니다. 끝난 뒤에 켜세요"
        if not alive("engine"):
            ports = [3001]
            if not alive("proxy"):
                try:
                    ports.append(int(settings()["listen"].rsplit(":", 1)[1]))
                except (ValueError, IndexError):
                    pass
            if settings().get("p2p") and not alive("p2pwin"):
                ports.append(P2P_PORT)
            free_ports_from_other_kits(ports)  # 다른 폴더의 유어위키가 켜져 있으면 먼저 끈다
            conflict = foreign_port_conflict(ports)  # 그래도 남았다면 유어위키가 아닌 프로그램이다 → 안내하고 멈춤
            if conflict:
                return conflict
        if not alive("engine"):
            try:  # 켤 때마다 표 전체를 읽는 검사를 피하는 색인(없을 때 한 번만 만든다)
                sys.path.insert(0, SCRIPTS)
                import fast_start
                fast_start.ensure(os.path.join(WIKI, "data.db"))
            except Exception:
                pass  # 못 만들어도 켜는 데는 지장 없다(조금 느릴 뿐)
            spawn("engine", [ENGINE, "3001", "--localhost"], "server.log", cwd=WIKI)
        if not alive("proxy"):
            start_proxy()
        if not alive("updater"):
            start_updater()
        if not alive("p2p"):
            start_p2p()

    def open_when_ready():  # 엔진이 준비되면 첫 화면을 브라우저로 열고, 공개를 켜 두었으면 다시 공개한다
        for _ in range(600):
            if port_open(3001):
                if open_browser:
                    webbrowser.open("http://" + settings()["listen"].replace("0.0.0.0", "127.0.0.1") + "/")
                if settings().get("public") and not alive("tunnel"):
                    tunnel(True)
                return
            time.sleep(1)
    threading.Thread(target=open_when_ready, daemon=True).start()
    return "켰습니다. 준비되면 첫 화면이 자동으로 열립니다"


def sweep_wiki_processes(root=None, include_panel=False):
    """어떤 유어위키 폴더(기본: 이 폴더)의 위키 프로그램(엔진·중계 서버·갱신기·P2P·터널)이 남아 있으면 끈다(Windows).
    설치·가져오기·내보내기 같은 작업의 프로세스는 건드리지 않는다(명령줄로 구분). include_panel 이면 그 폴더의 관리판도 끈다."""
    if not WIN:
        return
    root = os.path.abspath(root or ROOT)
    pat = r"offline_proxy\.py|updater\.py|p2p\.py|hub_server\.py" + (r"|panel\.py" if include_panel else "")
    ps = ("$root = '" + root.replace("'", "''") + "'; "
          "Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne " + str(os.getpid()) + " -and "
          "$_.ExecutablePath -and $_.ExecutablePath.StartsWith($root) -and ("
          "$_.Name -in @('main.amd64.exe','cloudflared.exe') -or "
          r"($_.Name -match '^pythonw?\.exe$' -and $_.CommandLine -match '" + pat + "')) } | "
          "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], creationflags=NO_WINDOW,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wiki_ports():
    ports = [3001, P2P_PORT]
    try:
        ports.append(int(settings()["listen"].rsplit(":", 1)[1]))
    except (ValueError, IndexError):
        pass
    return ports


def stop_wiki():
    """위키를 확실히 끈다: 기억해 둔 프로세스를 끄고, 남은 것은 폴더 안 프로세스를 찾아 끄고, 포트가 비었는지 확인한다."""
    with lock:
        for n in ("tunnel", "p2p", "p2ptunnel", "p2pwin", "updater", "proxy", "engine"):
            stop(n)
        sweep_wiki_processes()
        busy_ports = []
        for _ in range(20):  # 최대 10초 기다리며 포트가 닫히는지 본다
            busy_ports = [p for p in wiki_ports() if port_open(p)]
            if not busy_ports:
                break
            time.sleep(0.5)
    if busy_ports:
        who = port_owner(busy_ports[0])
        return (f"껐지만 포트 {busy_ports[0]} 이 아직 열려 있습니다" + (f": {who[1]}" if who and who[1] else "")
                + " — 작업 관리자에서 그 프로그램을 끄세요")
    return "껐습니다(엔진·중계 서버·동기화·P2P·터널 모두 종료, 포트 닫힘 확인)"


_job = None  # 관리판이 어떤 이유로든 끝나면 윈도우가 딸린 프로그램도 함께 끄도록 하는 작업 개체(닫히면 전부 종료)


def kill_children_on_exit():
    """Windows 작업 개체(Job Object): 관리판 창·콘솔을 그냥 닫아도 엔진·중계 서버·동기화 같은 딸린 프로세스가 남지 않게 한다."""
    global _job
    if not WIN:
        return
    try:
        import ctypes
        from ctypes import wintypes

        class IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in ("r", "w", "o", "rb", "wb", "ob")]

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

        class EXT(ctypes.Structure):
            _fields_ = [("Basic", BASIC), ("Io", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        # 64비트에서 핸들이 잘리지 않도록 인자·반환 형식을 선언한다(안 하면 조용히 실패한다)
        k.CreateJobObjectW.restype = wintypes.HANDLE
        k.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k.GetCurrentProcess.restype = wintypes.HANDLE
        job = k.CreateJobObjectW(None, None)
        info = EXT()
        info.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if job and k.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)) \
                and k.AssignProcessToJobObject(job, k.GetCurrentProcess()):
            _job = job  # 핸들을 붙잡아 둔다(프로세스가 끝나 핸들이 닫히면 작업 개체 안의 프로세스가 모두 종료됨)
    except Exception:  # 작업 개체를 못 만들어도 관리판은 그대로 동작한다(다음 시작 때 정리)
        _job = None


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


def tunnel(on):
    if not on:
        stop("tunnel")
        _cache.pop("url", None)
        return "공개를 껐습니다"
    if not alive("proxy"):
        return "먼저 위키를 켜세요"
    if alive("tunnel"):
        return "이미 공개 중입니다"
    try:
        import cloudflared
        exe = cloudflared.ensure()
    except Exception as e:
        return f"터널 프로그램을 받지 못했습니다: {e}"
    open(os.path.join(ROOT, "tunnel.log"), "w").close()
    spawn("tunnel", [exe, "tunnel", "--no-autoupdate", "--url", "http://" + settings()["listen"]], "tunnel.log")
    return "공개를 시작했습니다. 잠시 뒤 공개 주소가 표시됩니다"


def set_p2p(on=None, hubs=None, friends=None, open_mode=None):
    import p2p
    s = settings()
    if on is not None:
        s["p2p"] = on
    if open_mode in ("tunnel", "direct", "both"):
        s["p2p_open"] = open_mode
    if hubs is not None:
        s["p2p_hubs"] = [u for u in (p2p.norm_url(x) for x in re.split(r"[\s,]+", hubs)) if u]
    if friends is not None:
        s["p2p_friends"] = [x.lower() for x in re.split(r"[\s,]+", friends) if p2p.HEX64.match(x.lower())]
    save_settings(s)
    if alive("engine"):  # 켜져 있으면 바뀐 설정으로 다시 띄운다
        with lock:
            start_updater()
            start_p2p()
    if on is False:
        for n in ("p2p", "p2ptunnel", "p2pwin"):
            stop(n)
    return "P2P 설정을 바꿨습니다"


def p2p_status():
    out = {"hubs_alive": 0, "nodes": 0, "proven": 0, "friends": 0, "banned": 0, "audits_ok": 0, "unaudited": 0,
           "sent": 0, "received_today": 0, "id": "", "peers_found": 0, "peers_alive": 0, "window_url": "",
           "dht_ok": False}
    if not os.path.exists(os.path.join(WIKI, "data.db")):
        return out
    try:
        import p2p
        out["id"] = p2p.my_id(WIKI)
        q = p2p.init(sqlite3.connect(os.path.join(WIKI, "updater.db"), timeout=5))
        now = time.time()
        one = lambda sql, *a: q.execute(sql, a).fetchone()[0]  # noqa: E731
        out["hubs_alive"] = one("select count(*) from hubs where last_ok > ?", now - 600)
        out["nodes"] = one("select count(*) from nodes where banned = 0")
        out["proven"] = one("select count(*) from nodes where banned = 0 and trusted = 0 and audits_ok >= ? "
                            "and audits_bad = 0", p2p.PROBATION)
        out["friends"] = one("select count(*) from nodes where trusted = 1")
        out["banned"] = one("select count(*) from nodes where banned = 1")
        out["audits_ok"] = one("select coalesce(sum(audits_ok), 0) from nodes")
        out["unaudited"] = one("select count(*) from shared where src = 'p2p' and audited = 0")
        out["sent"] = one("select count(*) from shared where src = 'namu'")
        out["received_today"] = one("select count(*) from shared where src = 'p2p' and at > ?", now - 86400)
        out["peers_found"] = one("select count(*) from peers where url is not null and url != ''")
        out["peers_alive"] = one("select count(*) from peers where last_ok > ?", now - 600)
        out["window_url"] = p2p.get_meta(q, "dht_url")
        out["dht_ok"] = now - float(p2p.get_meta(q, "dht_at", "0") or 0) < p2p.DHT_EVERY * 2 and bool(out["window_url"])
        q.close()
    except sqlite3.Error:
        pass
    return out


EXPORT_DIR = os.path.join(ROOT, "export")


IMPORT_DIR = os.path.join(ROOT, "import")


def import_files():
    """import 폴더에 둔 openNAMU 형식 파일(.db .sqlite .sqlite3)."""
    try:
        return sorted(f for f in os.listdir(IMPORT_DIR) if f.lower().endswith((".db", ".sqlite", ".sqlite3")))
    except OSError:
        return []


def run_import(name, rng):
    if alive("import"):
        return "이미 가져오는 중입니다"
    if not os.path.exists(os.path.join(WIKI, "data.db")):
        return "아직 설치되지 않았습니다"
    if name not in import_files():
        return "import 폴더에서 파일을 고르세요"
    if alive("engine") or alive("export") or alive("install"):
        return "먼저 위키를 끄세요(가져오기는 위키 DB 를 바꾸므로 위키가 꺼져 있을 때만 합니다)"
    remember(import_range=rng)
    open(os.path.join(ROOT, "import.log"), "w").close()
    spawn("import", [sys.executable, os.path.join(SCRIPTS, "wiki_pack.py"), "import", WIKI,
                     os.path.join(IMPORT_DIR, name), "--range", rng], "import.log")
    return "가져오기를 시작했습니다. 끝나면 [켜기]로 위키를 켜세요"


def run_export(target, rng="all"):
    if target not in ("mediawiki", "dokuwiki", "markdown", "opennamu"):
        return "알 수 없는 형식입니다"
    if alive("import"):
        return "가져오는 중입니다. 끝난 뒤에 내보내세요"
    if alive("export"):
        return "이미 내보내는 중입니다"
    if not os.path.exists(os.path.join(WIKI, "data.db")):
        return "아직 설치되지 않았습니다"
    if alive("engine") and not port_open(3001):
        return "위키 엔진이 시작하는 중입니다. 준비된 뒤에 다시 누르세요"
    os.makedirs(EXPORT_DIR, exist_ok=True)
    open(os.path.join(ROOT, "export.log"), "w").close()
    remember(export_range=rng)
    if target == "opennamu":
        spawn("export", [sys.executable, os.path.join(SCRIPTS, "wiki_pack.py"), "export", WIKI, "--range", rng],
              "export.log")
        return "내보내기를 시작했습니다(openNAMU 형식은 변환이 없어 빠릅니다)"
    spawn("export", [sys.executable, os.path.join(SCRIPTS, "convert_wiki.py"), WIKI, "--to", target, "--range", rng],
          "export.log")
    return "내보내기를 시작했습니다. 남은 시간은 진행 줄에 표시됩니다(전체 문서면 CPU 4개 기준 약 1시간)"


def tunnel_url():
    import re
    if not alive("tunnel"):
        return ""
    for line in tail("tunnel.log", 200):
        m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
        if m:
            return m.group(0)
    return ""


KIT_VERSION = "2.0.3"
P2P_PORT = 3002  # P2P 창구(읽기 전용)


def fmt_secs(sec):
    sec = int(sec)
    if sec < 3600:
        return f"{max(1, sec // 60)}분"
    if sec < 86400:
        return f"{sec // 3600}시간 {sec % 3600 // 60}분"
    return f"{sec // 86400}일 {sec % 86400 // 3600}시간"


def dir_size(path):
    total = 0
    for dp, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    return total


def info():
    """키트·엔진·데이터 정보(1분 동안 기억해 둠: 색인 폴더 크기 계산이 느릴 수 있어서)."""
    if time.time() - _cache.get("info_at", 0) < 60:
        return _cache["info"]
    src = json.load(open(os.path.join(ROOT, "sources.json"), encoding="utf-8"))
    m = re.search(r"/download/([^/]+)/", src["tools"]["opennamu"]["url"])
    edition = {}
    try:
        edition = json.load(open(os.path.join(WIKI, "edition.json"), encoding="utf-8"))
    except (OSError, ValueError):
        pass
    gb = lambda n: round(n / 1e9, 1)  # noqa: E731
    data_files = sum(os.path.getsize(os.path.join(ROOT, "data", f))
                     for f in os.listdir(os.path.join(ROOT, "data"))) if os.path.isdir(os.path.join(ROOT, "data")) else 0
    db = os.path.join(WIKI, "data.db")
    out = {"kit": KIT_VERSION, "engine": "openNAMU " + (m.group(1) if m else "?"),
           "edition": edition.get("date", "?"), "db_gb": gb(os.path.getsize(db)) if os.path.exists(db) else 0,
           "index_gb": gb(dir_size(os.path.join(WIKI, "data", "bleve"))), "dump_gb": gb(data_files)}
    _cache["info"], _cache["info_at"] = out, time.time()
    return out


ARIA_RE = re.compile(r"\[#\w+ ([\d.]+)(\w+)/([\d.]+)(\w+)\((\d+)%\)(?: CN:\d+)?(?: DL:([\d.]+)(\w+))?(?: ETA:(\w+))?\]")
UNITS = {"B": "B", "KiB": "KB", "MiB": "MB", "GiB": "GB", "TiB": "TB"}


def eta_ko(s):
    """aria2 의 5h3m20s 같은 남은 시간을 한국어로."""
    out = []
    for num, unit in re.findall(r"(\d+)([hms])", s or ""):
        out.append(num + {"h": "시간", "m": "분", "s": "초"}[unit])
    return " ".join(out[:2]) or "계산 중"


def korean_log(lines):
    """설치 기록 중 영어로 나오는 다운로드 도구(aria2)의 진행 표시를 한국어로 바꾸고, 표 모양 잡줄은 뺀다."""
    out = []
    for raw in lines:
        for line in raw.replace("\r", "\n").split("\n"):
            t = line.strip()
            if not t:
                continue
            m = ARIA_RE.search(t)
            if m:
                done, du, total, tu, pct, sp, su, eta = m.groups()
                speed = f" · 속도 {sp}{UNITS.get(su, su)}/초" if sp else ""
                out.append(f"  받는 중: {done}{UNITS.get(du, du)} / {total}{UNITS.get(tu, tu)} ({pct}%){speed}"
                           f" · 남은 시간 약 {eta_ko(eta)}")
                continue
            if t.startswith(("***", "===", "---", "FILE:", "gid ", "Status Legend", "Download Results")) or \
                    re.match(r"^\w{6}\|", t) or t.startswith("(INPR)"):
                continue
            if t.startswith("(OK):download completed"):
                out.append("  받기 완료")
                continue
            if t.startswith("(ERR):error occurred"):
                out.append("  받는 중 오류가 났습니다")
                continue
            if "Exception:" in t or "errorCode=" in t:
                out.append("  다운로드 오류: " + t)
                continue
            out.append(line.rstrip() + "\n" if not line.endswith("\n") else line)
    return [x if x.endswith("\n") else x + "\n" for x in out]


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
          "color": s.get("color", "#3b5bdb"), "quit_on_close": s.get("quit_on_close", True),
          "running": {n: alive(n) for n in ("engine", "proxy", "updater", "install")}}
    st["ready"] = st["running"]["engine"] and port_open(3001)
    # 위키 엔진이 '켜지는 중'에는 data.db 를 열지 않는다.
    # (엔진이 시작할 때 DB 방식을 바꾸는 동안 다른 연결이 있으면 계속 기다리며 켜지지 않는다)
    # 꺼져 있거나 준비가 끝난 뒤에는 읽는다(준비 뒤에는 1분에 한 번). 마지막으로 읽은 수는 기억해 둔다.
    starting = st["running"]["engine"] and not st["ready"]
    if (st["installed"] and not starting and not st["running"]["install"]
            and (not st["ready"] or time.time() - _cache.get("docs_at", 0) > 60)):
        try:
            db = sqlite3.connect(pathlib.Path(WIKI, 'data.db').resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
            r = db.execute("select data from other where name = 'count_all_title'").fetchone()
            db.close()
            _cache["docs"], _cache["docs_at"] = (int(r[0]) if r else None), time.time()
            if _cache["docs"] is not None and s.get("docs") != _cache["docs"]:
                remember(docs=_cache["docs"])
        except Exception:
            pass
    st["docs"] = _cache.get("docs", s.get("docs"))
    try:
        st["data_date"] = json.load(open(os.path.join(WIKI, "edition.json"), encoding="utf-8-sig")).get("date", "")
    except (OSError, ValueError):
        st["data_date"] = ""
    try:
        q = sqlite3.connect(os.path.join(WIKI, "updater.db"), timeout=5)
        st["queue"] = q.execute("select count(*) from queue").fetchone()[0]
        st["fetched_today"] = q.execute("select count(*) from fetched where at > ?", (time.time() - 86400,)).fetchone()[0]
        # 문서 수 내역: 설치한 데이터의 문서 수(처음 동기화 때 기록) + 동기화로 늘어난 수, 그리고 최신판으로 갱신한 문서 수
        try:
            b = q.execute("select v from meta where k = 'base_docs'").fetchone()
            if not b and st["docs"] and not q.execute("select 1 from meta where k = 'count_synced'").fetchone():
                # 갱신기가 처음 돌기 전에는 '전체 문서 수'가 설치 때 받은 수 그대로이므로 그 값을 기준으로 적어 둔다
                q.execute("create table if not exists meta (k text primary key, v text)")
                q.execute("insert or ignore into meta values ('base_docs', ?)", (str(st["docs"]),))
                q.commit()
                b = (str(st["docs"]),)
            st["base_docs"] = int(b[0]) if b else None
            st["synced"] = (q.execute("select count(*) from fetched").fetchone()[0]
                            + q.execute("select count(*) from shared where src = 'p2p'").fetchone()[0])
        except (sqlite3.Error, ValueError):
            st["base_docs"], st["synced"] = None, 0
        # 대기열을 비우는 데 걸릴 시간: 나무위키 6초에 1건 + 최근 1시간 동안 P2P 로 받은 속도
        try:
            p2p_rate = q.execute("select count(*) from shared where src = 'p2p' and at > ?",
                                 (time.time() - 3600,)).fetchone()[0] / 3600
        except sqlite3.Error:
            p2p_rate = 0
        rate = (1 / 6 if s["sync"] != "off" else 0) + p2p_rate
        st["queue_eta"] = fmt_secs(st["queue"] / rate) if rate and st["queue"] else ""
        q.close()
    except Exception:
        st["queue"] = st["fetched_today"] = 0
    du = shutil.disk_usage(ROOT)
    st["disk_free_gb"] = round(du.free / 1e9, 1)
    st["install_log"] = korean_log(tail("install.log", 40))[-12:]
    st["updater_log"] = tail("updater.log", 8)
    st["info"] = info()
    st["update"] = dict(_cache.get("update") or {}, on=settings().get("update_notice", True))
    st["running"]["tunnel"] = alive("tunnel")
    st["running"]["p2p"] = alive("p2p")
    st["running"]["p2ptunnel"] = alive("p2ptunnel")
    st["running"]["p2pwin"] = alive("p2pwin")
    st["running"]["export"] = alive("export")
    st["running"]["import"] = alive("import")
    st["import_files"], st["import_dir"] = import_files(), IMPORT_DIR
    st["import_log"] = tail("import.log", 6)
    st["import_audit"] = {"pending": 0, "ok": 0, "bad": []}
    try:
        q = sqlite3.connect(pathlib.Path(WIKI, 'updater.db').resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
        for audited, n in q.execute("select audited, count(*) from shared where src = 'import' group by audited"):
            st["import_audit"]["ok" if audited else "pending"] = n
        st["import_audit"]["bad"] = [[k[9:], v] for k, v in q.execute("select k, v from meta where k like 'bad:file:%'")]
        q.close()
    except sqlite3.Error:
        pass
    st["export_range"], st["import_range"] = s.get("export_range", "all"), s.get("import_range", "all")
    try:
        import wiki_pack
        st["cutoff"] = wiki_pack.cutoff(WIKI)
    except Exception:
        st["cutoff"] = ""
    st["export_log"] = tail("export.log", 6)
    st["export_dir"] = EXPORT_DIR
    st["public_url"] = tunnel_url()
    st["p2p"] = dict(p2p_status(), on=bool(s.get("p2p")), open=s.get("p2p_open", "tunnel"), hubs_list=p2p_hubs(),
                     friends_list=s.get("p2p_friends", []), log=tail("p2p.log", 8))
    return st


PAGE = r"""<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>유어위키 관리판</title><style>
body{font-family:system-ui,sans-serif;max-width:760px;margin:24px auto;padding:0 16px;color:#222}
h1{font-size:22px}details.sec{border:1px solid #ddd;border-radius:8px;padding:12px 16px;margin:10px 0}details.sec>summary{cursor:pointer;list-style:none;display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}details.sec>summary::-webkit-details-marker{display:none}details.sec>summary::before{content:'▸';color:#888;width:1em}details.sec[open]>summary::before{content:'▾'}details.sec>summary h2{margin:0}details.sec[open]>summary{margin-bottom:10px}.sum{font-size:13px;color:#666}.sum .on{color:#0a0}
h2{font-size:16px;margin:0 0 8px}button{font-size:14px;padding:6px 12px;margin:2px;cursor:pointer}
.on{color:#0a0}.off{color:#999}pre{background:#f6f6f6;padding:8px;font-size:12px;white-space:pre-wrap;max-height:220px;overflow:auto}
.warn{font-size:12px;color:#a60}label{margin-right:12px}
</style><div id="upd" hidden style="background:#fff4e0;border:1px solid #f0c060;border-radius:8px;padding:10px 14px;margin:12px 0"></div>
<h1>유어위키 관리판</h1>
<p class="warn">이 데이터는 CC BY-NC-SA 2.0 KR입니다. 상업적 이용은 금지됩니다. 이 키트를 사용해 광고를 붙이거나 상업적으로 운영하는 것은 라이선스 위반입니다.</p>
<details class="sec" id="sec-st" data-default="1" open><summary><h2>상태</h2><span class="sum" id="sum-st"></span></summary><div id="st">불러오는 중…</div></details>
<details class="sec" id="sec-wiki" data-default="1" open><summary><h2>위키</h2><span class="sum" id="sum-wiki"></span></summary>
<button onclick="act('start')">켜기</button><button onclick="act('stop')">끄기</button>
<button onclick="openWiki()">위키 열기</button>
<label style="margin-left:12px;font-size:13px"><input type="checkbox" id="quitclose" onchange="api('/api/quit_on_close?on='+(this.checked?1:0))"> 관리판 창을 닫으면 위키도 끄고 종료(설치·내보내기 중에는 닫아도 계속)</label></details>
<details class="sec" id="sec-sync" data-default="0"><summary><h2>나무위키 최신판 동기화</h2><span class="sum" id="sum-sync"></span></summary>
<label><input type="radio" name="sync" value="off" onchange="setSync(this.value)"> 끄기</label>
<label><input type="radio" name="sync" value="queue" onchange="setSync(this.value)"> 요청한 문서만</label>
<label><input type="radio" name="sync" value="auto" onchange="setSync(this.value)"> 자동 따라잡기 (기본)</label>
<p style="font-size:13px;color:#555">robots.txt 준수 · 6초에 1건 이하 · 캡차·차단 감지 시 즉시 중단 · 같은 문서는 24시간에 한 번.
문서 화면의 「🔄 나무위키 최신판으로 갱신」 단추로 요청할 수 있습니다.</p>
<div id="sync"></div><pre id="ulog"></pre></details>
<details class="sec" id="sec-p2p" data-default="0"><summary><h2>P2P 공유</h2><span class="sum" id="sum-p2p"></span></summary>
<label><input type="radio" name="p2p" value="0" onchange="api('/api/p2p?on=0').then(load)"> 끄기 (기본)</label>
<label><input type="radio" name="p2p" value="1" onchange="api('/api/p2p?on=1').then(load)"> 켜기</label>
<div id="p2pline" style="margin:8px 0;font-weight:bold"></div>
<ul style="font-size:13px;color:#444;margin:6px 0 8px;padding-left:18px;line-height:1.7">
<li><b>하는 일</b>: 나무위키 최신판을 따라잡는 속도를 높입니다. 켠 유어위키들이 서로 다른 문서를 받아 나누므로, 참여자가 많을수록 빨라집니다.
나무위키로 가는 요청은 늘지 않습니다(6초에 1건 그대로).</li>
<li><b>켜 두기만 하면 됩니다</b>: 다른 유어위키를 알아서 찾아 주고받습니다. 운영하는 서버는 없습니다(BitTorrent 공용 연결망을 씁니다).</li>
<li><b>켜면 생기는 일</b>: 내 컴퓨터가 받은 문서를 다른 유어위키에게도 나눠 주므로 인터넷 사용량이 늘고, 참여하고 있다는 사실이 공용 연결망에 보입니다.
위키 화면과 내 IP 주소는 공개되지 않습니다(나가는 것은 서명된 나무위키 문서뿐, Cloudflare 임시 주소를 거침). 고급 설정에서 '직접 연결'을 고르면 Cloudflare 없이 열리는 대신 내 IP 가 보입니다.</li>
<li><b>엉터리 내용 막기</b>: 받은 문서의 일부를 나무위키에서 직접 다시 받아 맞춰 보고, 거짓이면 그 위키를 차단하고 받은 문서를 모두 되돌립니다.
그래서 처음 몇 시간은 조금씩만 받고, 검증이 쌓이면 빨라집니다.</li>
<li>회사·학교처럼 공용 연결망(UDP)을 막는 곳에서는 P2P 가 되지 않습니다(위키는 그대로 동작합니다).</li>
</ul>
<details style="font-size:13px"><summary>고급 설정</summary>
<div style="margin-top:8px">내 ID: <code id="myid" style="word-break:break-all"></code>
<button onclick="navigator.clipboard.writeText(document.getElementById('myid').textContent).catch(function(){})">복사</button></div>
<div style="margin-top:8px">창구 여는 방법 (다른 위키가 내 컴퓨터에서 문서를 가져가는 길):<br>
<label><input type="radio" name="popen" value="tunnel" onchange="api('/api/p2p_open?mode='+this.value).then(load)"> Cloudflare 임시 주소 (기본 · 내 IP 가 보이지 않음 · Cloudflare 무료 서비스에 기댐)</label><br>
<label><input type="radio" name="popen" value="direct" onchange="api('/api/p2p_open?mode='+this.value).then(load)"> 직접 연결 (Cloudflare 없이 · 공유기 포트 자동 열기(UPnP)나 IPv6 · <b>내 공인 IP 가 다른 참여자에게 보임</b>)</label><br>
<label><input type="radio" name="popen" value="both" onchange="api('/api/p2p_open?mode='+this.value).then(load)"> 둘 다 (직접 연결이 안 되는 위키는 Cloudflare 주소로 · 가장 튼튼함 · 내 IP 보임)</label>
<div style="color:#666">직접 연결은 공유기가 UPnP 를 켜 두었거나 공인 IPv6 가 있어야 됩니다. 통신사 공유 IP(CGNAT)에서는 안 됩니다.
Windows 가 '방화벽 허용' 창을 띄우면 허용을 눌러 주세요. 어느 쪽도 안 되면 받기는 그대로 되고 나눠 주기만 안 됩니다.</div></div>
<div style="margin-top:8px">친구 ID (선택. 적으면 그 위키가 준 문서는 검증 기간 없이 바로 믿고 받습니다. 한 줄에 하나):<br>
<textarea id="friends" rows="2" style="width:100%;font-size:13px"></textarea>
<button onclick="api('/api/p2p_friends?ids='+encodeURIComponent(document.getElementById('friends').value)).then(load)">친구 저장</button></div>
<div style="margin-top:8px">중계소 주소 (선택. 누군가 고정 주소 서버에 띄운 중계소가 있으면 적으세요. 한 줄에 하나):<br>
<textarea id="hubs" rows="2" style="width:100%;font-size:13px"></textarea>
<button onclick="api('/api/p2p_hubs?hubs='+encodeURIComponent(document.getElementById('hubs').value)).then(load)">중계소 저장</button></div>
<p style="font-size:12px;color:#a60">믿음은 주소가 아니라 ID 의 검증 실적으로만 쌓입니다. 검증 5건을 통과한 ID 만 '검증된 ID' 가 되고, 새 ID 들에게서는
모두 합쳐 한 시간에 200개까지만 받습니다. 내 문서와 절반 넘게 다른 내용은 나무위키에서 직접 확인합니다. 받은 문서는 역사에 어느 ID 에서 왔는지 남습니다.</p>
<div id="p2pst"></div><pre id="plog"></pre></details></details>
<details class="sec" id="sec-color" data-default="0"><summary><h2>위키 색</h2><span class="sum" id="sum-color"></span></summary>
<span id="swatch" style="display:inline-block;width:28px;height:28px;border-radius:6px;vertical-align:middle;border:1px solid #ccc"></span>
<select id="preset" onchange="if(this.value)setColor(this.value)">
<option value="">추천 색 고르기…</option>
<option value="#3b5bdb">인디고 블루 (기본)</option><option value="#1c3f94">딥 오션</option><option value="#1971c2">코발트</option>
<option value="#364fc7">로열 블루</option><option value="#5f3dc4">바이올렛</option><option value="#862e9c">자두</option>
<option value="#c2255c">라즈베리</option><option value="#c92a2a">레드</option><option value="#e8590c">오렌지</option>
<option value="#343a40">차콜</option><option value="#212529">블랙</option></select>
<input type="color" id="picker" onchange="setColor(this.value)" title="원하는 색 직접 고르기">
<span style="font-size:13px;color:#555">위키 맨 위 머리글 색입니다. 고르면 새로고침만으로 바로 바뀝니다.</span></details>
<details class="sec" id="sec-pub" data-default="0"><summary><h2>인터넷에 공개</h2><span class="sum" id="sum-pub"></span></summary>
<button onclick="pub(1)">공개하기</button><button onclick="act2('tunnel?on=0')">공개 끄기</button>
<div id="pubinfo" style="margin:6px 0;font-weight:bold"></div>
<p style="font-size:13px;color:#555">공유기 설정 없이 Cloudflare 임시 공개 주소(https)를 만듭니다. 켤 때마다 주소가 바뀝니다. [공개 끄기]를 누르기 전까지는 위키를 켤 때마다 다시 공개됩니다.<br>
<b>공개 전에</b>: 위키에서 먼저 가입해 관리자가 되고, 관리자 설정 → 권한에서 비로그인(ip) 사용자의 편집을 막으세요.
공개하는 순간 그 사이트의 운영 책임(권리 침해·게시중단 요청 대응 등)은 공개한 사람에게 있습니다.</p></details>
<details class="sec" id="sec-export" data-default="0"><summary><h2>내보내기</h2><span class="sum" id="sum-export"></span></summary>
<div style="margin-bottom:6px">범위: <label><input type="radio" name="erange" value="all"> 전체 문서</label>
<label><input type="radio" name="erange" value="changed"> <span class="cutlbl"></span> 이후 바뀐 문서만 (갱신기·P2P·직접 편집)</label></div>
<button onclick="exp('opennamu')">유어위키(openNAMU) 형식으로 내보내기</button><br>
<button onclick="exp('mediawiki')">MediaWiki 로 내보내기</button><button onclick="exp('dokuwiki')">DokuWiki 로 내보내기</button>
<button onclick="exp('markdown')">Markdown 으로 내보내기</button>
<div id="eprog" style="margin:6px 0;font-weight:bold"></div>
<p style="font-size:13px;color:#555">위키의 문서를 파일로 만들어 <span id="expdir"></span> 폴더에 저장합니다.<br>
유어위키(openNAMU): <code>yourwiki-opennamu-….db</code> → 다른 유어위키의 '가져오기'로 넣습니다. openNAMU 의 data.db 와 같은 형식(문서 표만, 계정·IP 기록은 넣지 않음)이라 '전체'는 새 openNAMU 에 그대로 써도 됩니다. '이후 바뀐 문서만'은 파일이 작아 나눠 주기 좋습니다.<br>
MediaWiki: <code>yourwiki-mediawiki.xml.gz</code> → <code>php maintenance/run.php importDump</code> 로 가져옵니다.<br>
DokuWiki: <code>yourwiki-dokuwiki.zip</code> → DokuWiki 폴더에 풀고 <code>php bin/indexer.php</code> 로 색인을 만듭니다.<br>
Markdown: <code>yourwiki-markdown.zip</code> → 문서마다 .md 파일 하나. Obsidian 같은 편집기에서 폴더째 엽니다.<br>
전체 문서(약 180만 개)는 CPU 4개 PC 기준 약 1시간, 디스크는 3~10GB 가 필요합니다. 진행 중에는 남은 시간이 표시됩니다.<br>
표·목록·각주·접기·틀 등 흔한 문법을 옮기고, 이미지와 #!html 은 옮기지 않습니다. 모든 문서의 출처·라이선스 고지는 그대로 남으니 지우지 마세요(CC BY-NC-SA 2.0 KR).</p>
<pre id="elog"></pre></details>
<details class="sec" id="sec-import" data-default="0"><summary><h2>가져오기 (openNAMU 형식)</h2><span class="sum" id="sum-import"></span></summary>
<div style="margin-bottom:6px">범위: <label><input type="radio" name="irange" value="all"> 파일의 전체 문서</label>
<label><input type="radio" name="irange" value="changed"> <span class="cutlbl"></span> 이후 바뀐 문서만</label></div>
<select id="ifile"></select> <button onclick="imp()">가져오기</button>
<div id="iprog" style="margin:6px 0;font-weight:bold"></div>
<p style="font-size:13px;color:#555">다른 유어위키가 내보낸 <code>yourwiki-opennamu-….db</code> 나 다른 openNAMU 위키의 <code>data.db</code> 를
<code id="impdir"></code> 폴더에 넣고 고르세요.<br>
문서마다 내 쪽보다 새 판만 역사 뒤에 이어 붙입니다. 내 쪽이 같거나 더 새로우면 건너뛰고, 지우기는 옮기지 않습니다.
가져온 판은 역사 요약에 [가져옴 파일이름] 이 붙습니다. <b>위키를 끈 상태에서만</b> 가져옵니다.<br>
<b>검증</b>: '나무위키 최신판' 이라고 적힌 판은 P2P 처럼 갱신기가 틈틈이 나무위키와 맞춰 봅니다(동기화가 켜져 있을 때).
하나라도 거짓이면 그 파일에서 가져온 문서를 모두 되돌리고 나무위키에서 다시 받습니다. 직접 편집한 판은 나무위키와 비교할 수 없어 검증하지 않습니다.</p>
<div id="iaudit" style="font-size:13px"></div>
<pre id="implog"></pre></details>
<details class="sec" id="sec-update" data-default="0"><summary><h2>새 판 알림</h2><span class="sum" id="sum-update"></span></summary>
<label><input type="checkbox" id="updon" onchange="api('/api/update_notice?on='+(this.checked?1:0)).then(load)"> GitHub 에 새 판이 나오면 알려 주기</label>
<button onclick="api('/api/update_check').then(load)">지금 확인</button>
<div id="updst" style="font-size:13px;color:#555;margin-top:6px"></div>
<p style="font-size:12px;color:#777">12시간에 한 번 GitHub(iamtalker/yourwiki)의 최신 릴리스만 확인합니다. 보내는 정보는 없고, 스스로 설치하지 않습니다.
새 판은 릴리스 내용을 보고 직접 받아 이 폴더에 덮어쓰세요(<code>wiki</code>·<code>data</code> 폴더는 그대로 두면 됩니다).</p></details>
<details class="sec" id="sec-install" data-default="0"><summary><h2>설치 · 데이터</h2><span class="sum" id="sum-install"></span></summary>
<button onclick="install('2026')">설치 / 다시 설치</button>
<p style="font-size:13px;color:#555">다시 설치하면 이미 받은 파일은 건너뜁니다. 위키는 설치 동안 꺼집니다.</p><pre id="inslog"></pre></details>
<script>
let listen="127.0.0.1:3000";
async function api(p){const r=await fetch(p,{method:'POST'});return (await r.json()).msg}
async function act(a){alert(await api('/api/'+a));load()}
async function act2(p){alert(await api('/api/'+p));load()}
async function pub(){if(confirm('위키를 인터넷에 공개할까요? 누구나 주소로 접속할 수 있게 됩니다.')){act2('tunnel?on=1')}}
async function setColor(c){await api('/api/color?c='+encodeURIComponent(c));load()}
async function install(e){if(confirm('설치할까요? 수십 분 이상 걸릴 수 있습니다.')){alert(await api('/api/install?edition='+e));load()}}
function rng(n){var x=document.querySelector('input[name='+n+']:checked');return x?x.value:'all'}
async function exp(t){var r=rng('erange');if(confirm((r==='changed'?'설치한 판 이후 바뀐 문서만':'전체 문서를')+' 내보낼까요?'+(r==='all'?' 문서가 많아 오래 걸리고 디스크 공간이 수~수십 GB 필요합니다.':''))){alert(await api('/api/export?to='+t+'&range='+r));load()}}
async function imp(){var f=document.getElementById('ifile').value;if(!f){alert('import 폴더에 파일을 넣은 뒤 고르세요');return}
if(confirm(f+' 을(를) 가져올까요? ('+(rng('irange')==='changed'?'설치한 판 이후 바뀐 문서만':'파일의 전체 문서')+')')){alert(await api('/api/import?file='+encodeURIComponent(f)+'&range='+rng('irange')));load()}}
async function setSync(m){await api('/api/sync?mode='+m);load()}
function openWiki(){window.open('http://'+listen.replace('0.0.0.0','127.0.0.1')+'/','_blank')}
function esc(t){return String(t==null?'':t).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]})}
function dot(b){return b?'<span class=on>●</span>':'<span class=off>○</span>'}
async function load(){const s=await (await fetch('/api/status')).json();listen=s.listen;
var i=s.info;document.getElementById('st').innerHTML=(s.installed?'설치됨 · 문서 '+(s.docs??'?').toLocaleString()+'개'+
(s.docs!=null&&s.base_docs!=null?' (갱신 '+(s.synced||0).toLocaleString()+' · 추가 '+Math.max(0,s.docs-s.base_docs).toLocaleString()+')':''):'아직 설치되지 않음')+
' · 디스크 여유 '+s.disk_free_gb+'GB<br><span style="font-size:13px;color:#555">유어위키 '+i.kit+' · 위키 엔진 '+i.engine+
' · 데이터 '+i.edition+'판 · 위키 DB '+i.db_gb+'GB · 검색 색인 '+i.index_gb+'GB · 받은 원본 '+i.dump_gb+'GB</span><br>'+dot(s.running.engine)+' 위키 엔진 '+dot(s.running.proxy)+' 중계 서버 '+
dot(s.running.updater)+' 갱신기 '+(s.running.install?'· <b>설치 진행 중</b>':'')+
(s.running.engine?(s.ready?'<br><b class=on>위키 준비됨 — [위키 열기]를 누르세요</b>':'<br><b>위키 엔진 시작 중… (문서가 많아 몇 분 걸릴 수 있습니다)</b>'):'');
document.querySelectorAll('input[name=sync]').forEach(x=>x.checked=x.value==s.sync);
var p=s.p2p;document.querySelectorAll('input[name=p2p]').forEach(x=>x.checked=x.value==(p.on?'1':'0'));
document.querySelectorAll('input[name=popen]').forEach(x=>x.checked=x.value==p.open);
document.getElementById('myid').textContent=p.id||'(설치 뒤에 만들어집니다)';
sum('sync',{off:'끔',queue:'요청한 문서만',auto:'자동 따라잡기'}[s.sync]+' · 대기열 '+s.queue+'개'+(s.queue_eta?' (약 '+s.queue_eta+')':''));
sum('p2p',!p.on?'꺼짐':(p.dht_ok?'<span class=on>켜짐</span> · 연결된 위키 '+p.peers_alive+'곳 · 24시간 받은 문서 '+p.received_today+'개':'켜짐 · 다른 위키 찾는 중'));
sum('color','<span style="display:inline-block;width:12px;height:12px;border-radius:3px;vertical-align:middle;background:'+esc(s.color)+'"></span> '+esc(s.color));
sum('pub',s.public_url?'<span class=on>공개 중</span> '+esc(s.public_url):(s.running.tunnel?'주소 만드는 중…':'꺼짐'));
var el=s.export_log.filter(function(l){return l.indexOf('진행')===0||l.indexOf('완료')===0}).pop();
sum('export',s.running.export?'내보내는 중 · '+esc(el||''):(el&&el.indexOf('완료')===0?'마지막: '+esc(el.split('→')[0]):''));
var uu=s.update||{};sum('update',uu.on===false?'꺼짐':(uu.newer?'<b>새 판 '+esc(uu.latest)+'</b>':(uu.latest?'최신 판':'')));
sum('install',(s.installed?'설치됨':'설치 전')+(s.running.install?' · <b>설치 중</b>':''));
document.getElementById('p2pline').innerHTML=!p.on?'':(!s.running.engine?'위키를 켜면 P2P 도 함께 켜집니다':
(p.dht_ok?'<span class=on>●</span> 참여 중 · 연결된 위키 '+p.peers_alive+'곳 · 최근 24시간 받은 문서 '+p.received_today+'개 · 내가 나눈 문서 '+p.sent+'개':
'<span class=off>●</span> 다른 유어위키를 찾는 중… (몇 분 걸릴 수 있습니다)'));
if(document.activeElement.id!=='hubs')document.getElementById('hubs').value=p.hubs_list.join('\n');
if(document.activeElement.id!=='friends')document.getElementById('friends').value=p.friends_list.join('\n');
document.getElementById('p2pst').innerHTML=p.on?(dot(s.running.p2p)+' P2P 작업자 · '+dot(s.running.p2pwin)+' P2P 창구 '+
(p.window_url?'<code>'+esc(p.window_url).split(' ').join('</code> · <code>')+'</code>':'(주소 만드는 중…)')+' · 공용 연결망(DHT) '+(p.dht_ok?'<span class=on>연결됨</span>':'<b>아직 안 됨</b>')+
'<br>찾은 위키 '+p.peers_found+'곳 (응답 중 '+p.peers_alive+'곳)'+(p.hubs_list.length?' · 중계소 '+p.hubs_list.length+'곳 중 '+p.hubs_alive+'곳 연결':'')+
' · 내가 나눈 문서 '+p.sent+'개 · 최근 24시간 받은 문서 '+p.received_today+'개'+
'<br><span style="font-size:13px">아는 ID '+p.nodes+'개 (친구 '+p.friends+' · 검증된 ID '+p.proven+') · 검증 통과 '+p.audits_ok+'건 · 검증 대기 '+p.unaudited+
'건 · 차단한 ID '+(p.banned?'<b style="color:#c00">'+p.banned+'개</b>':'0개')+'</span>'):'';
document.getElementById('plog').textContent=p.on?p.log.join(''):'';
document.getElementById('sync').textContent='대기열 '+s.queue+'개'+(s.queue_eta?' (지금 속도면 약 '+s.queue_eta+' 뒤 비움)':'')+' · 최근 24시간 받은 문서 '+s.fetched_today+'개';
document.getElementById('ulog').textContent=s.updater_log.join('');
document.getElementById('inslog').textContent=s.install_log.join('');
var u=s.update||{},ub=document.getElementById('upd');document.getElementById('updon').checked=u.on!==false;
ub.hidden=!(u.on!==false&&u.newer);
if(u.newer&&ub.dataset.v!==u.latest){ub.dataset.v=u.latest;ub.innerHTML='<b>새 판이 나왔습니다: '+esc(u.name||u.latest)+'</b> ('+esc(u.published||'')+') · 지금 '+esc(u.current)+
' · <a href="'+esc(u.url)+'" target=_blank>릴리스 보기</a>'+(u.notes?'<details style="margin-top:6px"><summary>달라진 점</summary><pre>'+esc(u.notes)+'</pre></details>':'')}
document.getElementById('updst').textContent=u.on===false?'알림 꺼짐':(u.latest?(u.newer?'새 판 '+u.latest+' 이 있습니다':'최신 판입니다 ('+u.current+', GitHub 최신 '+u.latest+')')+
(u.checked_at?' · 확인 '+new Date(u.checked_at*1000).toLocaleString():''):(u.error?'확인하지 못했습니다: '+u.error:'확인 전'));
document.getElementById('elog').textContent=s.export_log.join('');
var pl=s.export_log.filter(l=>l.startsWith('진행')||l.startsWith('완료')).pop();
document.getElementById('eprog').textContent=s.running.export?('내보내는 중 · '+(pl||'준비 중…')):(pl&&pl.startsWith('완료')?pl:'');
document.getElementById('expdir').textContent=s.export_dir;
document.querySelectorAll('.cutlbl').forEach(x=>x.textContent='설치한 판('+(s.cutoff||'?')+')');
if(!rng.init){rng.init=1;document.querySelectorAll('input[name=erange]').forEach(x=>x.checked=x.value==s.export_range);
document.querySelectorAll('input[name=irange]').forEach(x=>x.checked=x.value==s.import_range)}
var fs=document.getElementById('ifile'),fk=s.import_files.join('\n');if(fs.dataset.k!==fk){var keep=fs.value;fs.dataset.k=fk;
fs.innerHTML=s.import_files.length?s.import_files.map(f=>'<option>'+esc(f)+'</option>').join(''):'<option value="">(import 폴더가 비어 있음)</option>';if(keep)fs.value=keep}
document.getElementById('impdir').textContent=s.import_dir;document.getElementById('implog').textContent=s.import_log.join('');
var il=s.import_log.filter(l=>l.startsWith('진행')||l.startsWith('완료')||l.startsWith('가져온')).pop();
var ig=s.import_log.filter(l=>l.startsWith('가져온')).pop();
document.getElementById('iprog').textContent=s.running.import?('가져오는 중 · '+(il||'준비 중…')):(ig||il||'');
var ia=s.import_audit;document.getElementById('iaudit').innerHTML=(ia.pending||ia.ok?'검증 대기 '+ia.pending+'개 · 확인함 '+ia.ok+'개':'')+
ia.bad.map(b=>'<div style="color:#c00">거짓 내용이 확인되어 되돌린 파일: <b>'+esc(b[0])+'</b> — '+esc(b[1])+'</div>').join('');
sum('import',s.running.import?'<b>가져오는 중</b>':(s.import_files.length?'파일 '+s.import_files.length+'개':''));
document.getElementById('swatch').style.background=s.color;document.getElementById('picker').value=s.color;
var qc=document.getElementById('quitclose');if(qc&&document.activeElement!==qc)qc.checked=s.quit_on_close!==false;
document.getElementById('pubinfo').innerHTML=s.public_url?('공개 주소: <a href="'+s.public_url+'" target=_blank>'+s.public_url+'</a>'):(s.running.tunnel?'공개 주소를 만드는 중…':'')}
document.querySelectorAll('details.sec').forEach(function(d){
  try{var v=localStorage.getItem('kit-'+d.id);if(v!==null)d.open=v==='1'}catch(e){}
  d.addEventListener('toggle',function(){try{localStorage.setItem('kit-'+d.id,d.open?'1':'0')}catch(e){}})});
function esc(t){return String(t==null?'':t).replace(/[&<>"']/g,function(c){return '&#'+c.charCodeAt(0)+';'})}
function sum(k,h){var e=document.getElementById('sum-'+k);if(e&&e.innerHTML!==h)e.innerHTML=h}
window.addEventListener('pagehide',function(){try{navigator.sendBeacon('/api/bye')}catch(e){}});
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
        if path in ("/", "/api/status"):
            _bye["t"] = 0.0  # 창이 다시 열렸거나 새로 고침 — 닫힘 신호를 취소한다
        if path == "/":
            return self.send(200, PAGE, "text/html; charset=utf-8")
        if path == "/api/status":
            return self.send(200, json.dumps(status(), ensure_ascii=False), "application/json")
        self.send(404, "없음", "text/plain; charset=utf-8")

    def do_POST(self):
        u = urlsplit(self.path)
        q = parse_qs(u.query)
        if u.path == "/api/start":
            remember(wiki_on=True)
            msg = start_wiki()
        elif u.path == "/api/stop":
            remember(wiki_on=False)
            msg = stop_wiki()
        elif u.path == "/api/bye":  # 브라우저가 관리판 창을 닫을 때 보내는 신호(새로 고침이면 곧 취소됨)
            if settings().get("quit_on_close", True):
                _bye["t"] = time.time()
            msg = "ok"
        elif u.path == "/api/quit_on_close":
            on = (q.get("on") or ["1"])[0] == "1"
            remember(quit_on_close=on)
            msg = "관리판 창을 닫으면 위키도 " + ("끕니다" if on else "끄지 않습니다(다음에 관리판을 열 때 정리됨)")
        elif u.path == "/api/color":
            c = (q.get("c") or [""])[0]
            if re.fullmatch(r"#[0-9a-fA-F]{6}", c):
                st = settings()
                st["color"] = c
                save_settings(st)
                msg = "색을 바꿨습니다. 위키 페이지를 새로고침하세요"
            else:
                msg = "색 형식이 올바르지 않습니다"
        elif u.path == "/api/tunnel":
            on = (q.get("on") or ["1"])[0] == "1"
            msg = tunnel(on)
            if not on or "시작" in msg or "이미" in msg:
                remember(public=on)
        elif u.path == "/api/install":
            msg = run_install((q.get("edition") or ["2026"])[0])
        elif u.path == "/api/p2p":
            msg = set_p2p(on=(q.get("on") or ["0"])[0] == "1")
        elif u.path == "/api/p2p_hubs":
            msg = set_p2p(hubs=(q.get("hubs") or [""])[0])
        elif u.path == "/api/p2p_open":
            msg = set_p2p(open_mode=(q.get("mode") or [""])[0])
        elif u.path == "/api/p2p_friends":
            msg = set_p2p(friends=(q.get("ids") or [""])[0])
        elif u.path == "/api/update_check":
            import update_check
            _cache["update"] = update_check.check(force=True)
            msg = update_check.message(_cache["update"])
        elif u.path == "/api/update_notice":
            st = settings()
            st["update_notice"] = (q.get("on") or ["1"])[0] == "1"
            save_settings(st)
            msg = "새 판 알림을 " + ("켰습니다" if st["update_notice"] else "껐습니다")
        elif u.path == "/api/export":
            rng = "changed" if (q.get("range") or ["all"])[0] == "changed" else "all"
            msg = run_export((q.get("to") or [""])[0], rng)
        elif u.path == "/api/import":
            rng = "changed" if (q.get("range") or ["all"])[0] == "changed" else "all"
            msg = run_import((q.get("file") or [""])[0], rng)
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


def update_loop():
    """새 판 알림: 한 시간마다 들여다보되 GitHub 에는 12시간에 한 번만 묻는다(update_check.py). 알리기만 한다."""
    import update_check
    while True:
        if settings().get("update_notice", True):
            try:
                _cache["update"] = update_check.check()
            except Exception:
                pass
        time.sleep(3600)


def close_watcher(srv):
    """브라우저 창이 닫힌 지 10초가 지나도 다시 열리지 않으면 위키를 끄고 관리판을 끝낸다(설치·가져오기·내보내기 중에는 안 끝냄)."""
    while True:
        time.sleep(2)
        t = _bye["t"]
        if not t or time.time() - t < 10:
            continue
        if any(alive(n) for n in ("install", "import", "export")) or not settings().get("quit_on_close", True):
            _bye["t"] = 0.0
            continue
        print("관리판 창이 닫혀 위키를 끄고 종료합니다", flush=True)
        threading.Thread(target=srv.shutdown, daemon=True).start()  # serve_forever 가 끝나면 finally 에서 stop_wiki
        return


def main():
    kill_children_on_exit()
    cleanup_leftovers()
    os.makedirs(IMPORT_DIR, exist_ok=True)  # 가져올 파일을 넣는 곳
    threading.Thread(target=update_loop, daemon=True).start()
    srv = None
    for attempt in range(2):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", PANEL_PORT), Handler)
            break
        except OSError:
            if attempt == 0:
                free_ports_from_other_kits([PANEL_PORT])  # 다른 폴더의 유어위키 관리판이면 끄고 다시 시도
    if srv is None:
        owner = port_owner(PANEL_PORT)
        where = (owner[1] or f"PID {owner[0]}") if owner else "알 수 없는 프로그램"
        print(f"관리판 포트 {PANEL_PORT} 을(를) 유어위키가 아닌 프로그램이 쓰고 있습니다: {where}\n"
              "그 프로그램을 끄고 다시 실행하세요.", flush=True)
        sys.exit(1)
    threading.Thread(target=close_watcher, args=(srv,), daemon=True).start()
    url = f"http://127.0.0.1:{PANEL_PORT}/"
    print(f"유어위키 관리판: {url}  (끌 때는 [끄기]를 누르거나 관리판 창을 닫으세요)", flush=True)
    if "--no-browser" not in sys.argv:  # 자동 시험·서버처럼 브라우저가 필요 없을 때
        webbrowser.open(url)
    # 지난번에 위키를 켜 둔 채로 끝냈으면 다시 켠다(끄기를 누른 경우만 꺼진 채로 둔다)
    s = settings()
    if s.get("wiki_on") and os.path.exists(os.path.join(WIKI, "data.db")):
        print("지난번 설정대로 위키를 다시 켭니다" + (" (공개 포함, 주소는 새로 바뀝니다)" if s.get("public") else ""),
              flush=True)
        start_wiki(open_browser=False)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_wiki()


if __name__ == "__main__":
    main()
