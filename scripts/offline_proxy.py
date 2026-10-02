"""오프라인 중계 서버: 브라우저 ↔ (이 프로그램) ↔ openNAMU

openNAMU 는 CDN 주소와 외부 삽입(유튜브 등)을 프로그램 안에 고정해 두어서,
문서를 열 때마다 보는 사람의 IP 가 여러 외부 서비스로 전달된다.
이 중계 서버는 openNAMU 가 보낸 HTML 을 고쳐서
  1) CDN 파일은 설치 때 받아 둔 로컬 사본(/_kit/cdn/...)에서 불러오게 하고
  2) 영상·SNS 삽입(iframe)은 눌러야 열리는 링크로 바꾸고
  3) 모든 페이지에 붙는 트위터 스크립트는 뺀다.
  4) 아이콘은 iconify 서버에서 받지 않고, 동봉한 icons.json 으로 SVG 를 직접 그려 넣는다.
DB 의 문서 원문은 건드리지 않는다.

사용: python offline_proxy.py <assets 폴더> [--listen 127.0.0.1:3000] [--upstream 127.0.0.1:3001]
"""
import argparse
import html
import http.client
import http.server
import json
import mimetypes
import os
import pathlib
import re
import socketserver
import sqlite3
import sys
import time
import urllib.parse

CDN_HOSTS = ("cdn.jsdelivr.net", "cdnjs.cloudflare.com", "code.iconify.design")
CDN_RE = re.compile(r"https://(%s)/([^\"'\s)]+)" % "|".join(re.escape(h) for h in CDN_HOSTS))
DROP_SCRIPT_RE = re.compile(
    r"<script\b[^>]*\bsrc=[\"']https://(?:platform\.twitter\.com/widgets\.js|code\.iconify\.design/[^\"']+)"
    r"[\"'][^>]*>\s*</script>", re.I)
ICON_RE = re.compile(r'<span class="iconify" data-icon="ic:([\w-]+)"[^>]*>\s*</span>')
ICONS = {}
IFRAME_RE = re.compile(r"<iframe\b[^>]*?\bsrc=[\"']([^\"']+)[\"'][^>]*>.*?</iframe>", re.I | re.S)

# 삽입 주소 → (보여줄 이름, 원래 페이지 주소)
WATCH_RULES = [
    (re.compile(r"https://www\.youtube(?:-nocookie)?\.com/embed/([\w-]+)"), "유튜브",
     "https://www.youtube.com/watch?v={0}"),
    (re.compile(r"https://embed\.nicovideo\.jp/watch/(\w+)"), "니코니코 동화",
     "https://www.nicovideo.jp/watch/{0}"),
    (re.compile(r"https://player\.vimeo\.com/video/(\d+)"), "비메오", "https://vimeo.com/{0}"),
    (re.compile(r"https://tv\.kakao\.com/embed/player/cliplink/(\d+)"), "카카오TV",
     "https://tv.kakao.com/v/{0}"),
    (re.compile(r"https://tv\.naver\.com/embed/(\d+)"), "네이버TV", "https://tv.naver.com/v/{0}"),
]


def unembed(m):
    src = html.unescape(m.group(1))
    if src.startswith("/") or src.startswith("http://127.0.0.1") or src.startswith("http://localhost"):
        return m.group(0)
    for rule, name, fmt in WATCH_RULES:
        r = rule.match(src)
        if r:
            return link(fmt.format(*r.groups()), f"▶ {name}에서 보기")
    for key in ("twitframe.com/show?url=", "facebook.com/plugins/post.php?href="):
        if key in src:
            return link(urllib.parse.unquote(src.split(key, 1)[1].split("&")[0]), "▶ 원본 게시물 보기")
    host = urllib.parse.urlsplit(src).netloc or "외부"
    return link(src, f"▶ 외부 콘텐츠 열기 ({host})")


def link(url, text):
    return (f'<a class="kit-embed-link" href="{html.escape(url)}" target="_blank" '
            f'rel="noopener noreferrer">{html.escape(text)}</a>')


def icon_svg(m):
    icon = ICONS.get("icons", {}).get(m.group(1))
    if not icon:
        return ""
    w = icon.get("width", ICONS.get("width", 24))
    h = icon.get("height", ICONS.get("height", 24))
    return (f'<svg class="iconify" width="1em" height="1em" viewBox="0 0 {w} {h}" '
            f'style="vertical-align:-0.125em" aria-hidden="true">{icon["body"]}</svg>')


KIT_NOTICE = ('<div style="text-align:center;font-size:12px;color:#888;padding:12px">'
              '유어위키 · 이 데이터는 CC BY-NC-SA 2.0 KR입니다. 상업적 이용은 금지됩니다. 이 키트를 사용해 광고를 붙이거나 상업적으로 운영하는 것은 라이선스 위반입니다. 문서 출처: 나무위키 기여자들</div>')


def rewrite_html(body):
    body = DROP_SCRIPT_RE.sub("", body)
    body = ICON_RE.sub(icon_svg, body)
    body = IFRAME_RE.sub(unembed, body)
    body = CDN_RE.sub(lambda m: f"/_kit/cdn/{m.group(1)}/{m.group(2)}", body)
    return body.replace("</body>", KIT_NOTICE + "</body>", 1)


# 유어위키 색: 관리판에서 고른 색(panel.json 의 "color")을 상단 머리글에만 입힌다. 파일이 바뀌면 곧바로 반영.
PANEL_JSON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "panel.json")
DEFAULT_COLOR = "#3b5bdb"
_theme = {"mtime": None, "css": ""}


def darker(hex_color, f=0.88):
    h = hex_color.lstrip("#")
    return "#%02x%02x%02x" % tuple(int(int(h[i:i + 2], 16) * f) for i in (0, 2, 4))


def theme_css():
    try:
        mtime = os.path.getmtime(PANEL_JSON)
    except OSError:
        mtime = 0
    if mtime != _theme["mtime"]:
        color = DEFAULT_COLOR
        try:
            c = json.load(open(PANEL_JSON, encoding="utf-8")).get("color", DEFAULT_COLOR)
            if re.fullmatch(r"#[0-9a-fA-F]{6}", c):
                color = c
        except (OSError, ValueError):
            pass
        _theme["css"] = ("<style>"
                         f"header#main{{background-color:{color}!important}}"
                         "header#main a,header#main a#logo{color:#fff!important}"
                         f"header#main a:hover,header a#logo:hover,.top_cel a:hover{{background-color:{darker(color)}!important}}"
                         "header#section{background-color:#fff}"
                         ".top_cel_in{background:#fff!important;border:1px solid #ddd;box-shadow:0 4px 12px rgba(0,0,0,.12)}"
                         "header#main .top_cel_in a{color:#222!important}"  # 위의 header#main a(흰색)보다 세야 한다
                         "header#main .top_cel_in a:hover{background-color:#eef!important;color:#222!important}"
                         f".kit-badge{{background:{color}!important}}"
                         f"#nav_bar{{background-color:{color}!important}}#nav_bar a{{color:#fff!important}}"
                         f"button.search_button,button.search_button:hover{{background:{color}!important;color:#fff!important}}"
                         f"button.search_button:hover{{background:{darker(color)}!important}}"
                         f".kit-random{{background:{color}!important}}.kit-random:hover{{background:{darker(color)}!important}}"
                         f"#nav_bar a:hover{{background-color:{darker(color)}!important}}"
                         + MOBILE_CSS + "</style>")
        _theme["mtime"] = mtime
    return _theme["css"]


# 휴대폰(좁은 화면): 검색줄을 한 줄로, 넓은 표는 옆으로 밀어 보기, 떠 있는 단추는 아이콘만, 아래쪽 여백
MOBILE_CSS = (
    ".table_safe{overflow-x:auto;-webkit-overflow-scrolling:touch;max-width:100%}"
    "@media (max-width:720px){"
    "form.only_mobile{display:flex!important;align-items:center;gap:4px;flex-wrap:nowrap;padding:4px 8px;box-sizing:border-box}"
    "form.only_mobile input.search{flex:1 1 auto;min-width:0;width:auto!important;margin:0!important}"
    "form.only_mobile .search_button,form.only_mobile .kit-random{flex:0 0 auto;margin:0!important}"
    ".table_safe td,.table_safe th{word-break:keep-all;min-width:3.5em}"
    ".kit-refresh .kit-label,.kit-badge .kit-label{display:none}"
    ".kit-refresh,.kit-badge{padding:8px 10px!important;left:8px!important;bottom:8px!important}"
    "body{padding-bottom:56px}"
    "}")

LAYOUT_JS = """<script>(function(){
/* 목록·도구·사용자 메뉴를 오른쪽에서 왼쪽 로고(유어위키) 옆으로 옮긴다. 검색창은 오른쪽에 둔다. */
var left=document.querySelector('header#main #left'),cels=document.querySelectorAll('header#main #right > .top_cel');
if(!left||!cels.length)return;left.style.display='inline-flex';left.style.alignItems='center';left.style.gap='4px';
cels.forEach(function(c){left.appendChild(c)})})();
(function(){/* 검색 단추를 나무위키처럼: 검색은 돋보기, 바로 가기는 오른쪽 화살표, 순서는 돋보기 → 화살표 */
var arrow='<svg width="1em" height="1em" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M4 11h12.17l-5.59-5.59L12 4l8 8-8 8-1.41-1.41L16.17 13H4z"/></svg>';
var lens='<svg width="1em" height="1em" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M15.5 14h-.79l-.28-.27A6.47 6.47 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14"/></svg>';
document.querySelectorAll('button.search_button#goto').forEach(function(g){g.innerHTML=arrow;g.title='바로 가기';
var sb=g.parentNode.querySelector('button.search_button#search');if(sb){sb.innerHTML=lens;sb.title='검색';g.parentNode.insertBefore(sb,g)}})})();</script>"""

SUGGEST_JS = """<script>(function(){
var inputs=document.querySelectorAll('input[type=search],input[name=search]');if(!inputs.length)return;
var dl=document.createElement('datalist');dl.id='kit-suggest';document.body.appendChild(dl);var timer,last='';
inputs.forEach(function(inp){
var f=inp.form||inp.parentElement;if(f&&!f.querySelector('.kit-random')){var a=document.createElement('a');
a.href='/random';a.className='kit-random';a.title='아무 문서나 보기';a.innerHTML='<svg viewBox="0 0 24 24" style="width:100%;height:100%" aria-hidden="true"><path fill="currentColor" d="M10.59 9.17 5.41 4 4 5.41l5.17 5.17zM14.5 4l2.04 2.04L4 18.59 5.41 20 17.96 7.46 20 9.5V4zm.33 9.41-1.41 1.41 3.13 3.13L14.5 20H20v-5.5l-2.04 2.04z"/></svg>';a.style.cssText='display:inline-flex;align-items:center;justify-content:center;box-sizing:border-box;width:32px;height:32px;padding:3px;border-radius:6px;color:#fff;text-decoration:none;vertical-align:middle;margin-right:4px';inp.parentNode.insertBefore(a,inp)}
inp.setAttribute('list','kit-suggest');inp.setAttribute('autocomplete','off');
inp.addEventListener('input',function(){var q=inp.value.trim();clearTimeout(timer);if(!q||q===last)return;
timer=setTimeout(function(){last=q;fetch('/_kit/suggest?q='+encodeURIComponent(q)).then(function(r){return r.json()})
.then(function(list){dl.innerHTML='';list.forEach(function(t){var o=document.createElement('option');o.value=t;dl.appendChild(o)})})
.catch(function(){})},200)})})})();</script>"""


def add_suggest(body):
    """모든 페이지의 검색창에 제목 자동완성을 붙인다."""
    body = body.replace("</head>", theme_css() + "</head>", 1)
    return body.replace("</body>", LAYOUT_JS + SUGGEST_JS + "</body>", 1)


def checked_at(queue_db, title):
    """24시간 안에 나무위키에서 확인한 문서면 그 시각(epoch), 아니면 None."""
    try:
        db = sqlite3.connect(queue_db, timeout=5)
        row = db.execute("select at from fetched where title = ?", (title,)).fetchone()
        db.close()
    except sqlite3.Error:
        return None
    return row[0] if row and time.time() - row[0] < 24 * 3600 else None


NAMU_AUTHORS = ("나무위키 덤프", "유어위키 갱신기", "유어위키 P2P")  # 나무위키에서 가져온 판의 편집자 이름


def from_namu(queue_db, title):
    """이 문서가 나무위키에서 온 적이 있는가(역사에 나무위키 덤프·갱신기·P2P 판이 있는가).
    우리가 만든 문서나 직접 새로 쓴 문서에는 '나무위키 최신판으로 갱신' 단추가 맞지 않다. 확인하지 못하면 True."""
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(queue_db)), "data.db")
        db = sqlite3.connect(pathlib.Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
        try:
            return db.execute("select 1 from history where title = ? and ip in (?, ?, ?) limit 1",
                              (title,) + NAMU_AUTHORS).fetchone() is not None
        finally:
            db.close()
    except sqlite3.Error:
        return True


_panel = {"mtime": None, "d": {}}


def panel_setting(key, default):
    """관리판에서 고른 설정(panel.json). 파일이 바뀔 때만 다시 읽는다."""
    try:
        mtime = os.path.getmtime(PANEL_JSON)
    except OSError:
        return default
    if mtime != _panel["mtime"]:
        try:
            d = json.load(open(PANEL_JSON, encoding="utf-8"))
            _panel["d"] = d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            _panel["d"] = {}
        _panel["mtime"] = mtime
    return _panel["d"].get(key, default)


def badge_on():
    """관리판에서 '최신판 갱신 표시'를 켜 두었는가(refresh_badge, 기본 켬)."""
    return panel_setting("refresh_badge", True) is not False


# 이미지 파일이 위키에 없을 때 오픈나무가 그리는 자리: <a class="opennamu_not_exist_link" title="파일:이름" href="/upload/…">(파일:이름)</a>
NOIMG_RE = re.compile(r'<a class="opennamu_not_exist_link" title="([^"]*)" href="/upload/[^"]*">\([^<]*\)</a>')

# 이미지가 다른 링크의 글자로 들어 있을 때([[문서|[[파일:…]]]])는 링크 글자가 '(파일:이름)' 으로 나온다. 글자가 이것뿐인 링크는 통째로 지운다.
NOIMG_LABEL_RE = re.compile(r'<a [^>]*>\(파일:[^<()]*\)</a>')


def hide_missing_images(body):
    """관리판에서 켰으면, 위키에 없는 이미지 자리('(파일:이름)')를 화면에서 지운다. 문서 원문은 건드리지 않는다."""
    if panel_setting("hide_missing_images", False) is not True:
        return body
    return NOIMG_LABEL_RE.sub("", NOIMG_RE.sub("", body))


EXT = None  # 내 PC 에서만 쓰는 확장(local_ext/proxy_ext.py). 파일이 없으면 아무 일도 하지 않는다.


def load_ext():
    global EXT
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "local_ext", "proxy_ext.py")
    if not os.path.exists(path):
        return
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("proxy_ext", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.init(sys.modules[__name__])
        EXT = mod
    except Exception as e:  # 확장이 고장 나도 중계 서버는 그대로 동작한다
        print("확장을 불러오지 못했습니다:", e)


def ext_decorate(body, path, queue_db):
    if EXT is None:
        return body
    try:
        return EXT.decorate(body, path, queue_db)
    except Exception:
        return body


def refresh_button(body, path, queue_db=""):
    """문서 화면에 '나무위키 최신판으로 갱신' 단추를 붙인다(갱신 대기열이 있을 때만).

    24시간 안에 이미 확인한 문서는 단추 대신 '최신 버전' 표시를 보여 준다.
    """
    if not path.startswith("/w/") or not badge_on():
        return body
    title = urllib.parse.unquote(path[3:].split("?")[0].split("#")[0])
    if not title or title.startswith(("category:", "틀:")):
        return body
    if queue_db and not from_namu(queue_db, title):
        return body
    at = checked_at(queue_db, title) if queue_db else None
    if at:
        t = time.localtime(at)
        when = time.strftime("%H:%M", t) if time.strftime("%Y%m%d", t) == time.strftime("%Y%m%d") \
            else time.strftime("%m/%d %H:%M", t)
        badge = ('<span class="kit-badge" title="24시간 안에 나무위키에서 확인한 문서입니다" '
                 'style="position:fixed;left:12px;bottom:12px;z-index:2147483000;color:#fff;'
                 'font-size:13px;padding:7px 12px;border-radius:18px;box-shadow:0 2px 6px rgba(0,0,0,.25)">'
                 f'✔<span class="kit-label"> 최신 버전 ({"오늘 " if ":" in when and "/" not in when else ""}{when} 확인)</span></span>')
        m = re.search(r"<body[^>]*>", body)
        return body[:m.end()] + badge + body[m.end():] if m else body
    # 화면 왼쪽 아래에 떠 있게 한다. 문서 안에 넓은 표가 있으면 오른쪽 끝에 붙인 단추가 화면 밖으로 밀려나기 때문.
    btn = ('<a class="kit-refresh" href="/_kit/refresh?title=' + urllib.parse.quote(title) + '" rel="nofollow" '
           'title="이 문서를 나무위키 최신판으로 갱신" '
           'style="position:fixed;left:12px;bottom:12px;z-index:2147483000;background:#2a7;color:#fff;'
           'font-size:13px;padding:7px 12px;border-radius:18px;text-decoration:none;'
           'box-shadow:0 2px 6px rgba(0,0,0,.25)">🔄<span class="kit-label"> 나무위키 최신판으로 갱신</span></a>')
    m = re.search(r"<body[^>]*>", body)
    return body[:m.end()] + btn + body[m.end():] if m else body


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    upstream = ("127.0.0.1", 3001)
    cdn_dir = ""
    queue_db = ""
    hub = False

    COOLDOWN = 24 * 3600

    def _qdb(self):
        db = sqlite3.connect(self.queue_db, timeout=30)
        db.executescript("create table if not exists queue (title text primary key, priority int, reason text, added real);"
                         "create table if not exists fetched (title text primary key, at real, namu_modified text);")
        return db

    def _send_bytes(self, body, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _refresh(self):
        """갱신 단추: 문서를 대기열 맨 앞(우선순위 9)에 넣고, 갱신이 끝나면 문서로 자동으로 돌아가는 화면을 보낸다.

        실제로 받는 건 updater.py 이고, 이 화면은 /_kit/refresh_status 를 물어보며 기다린다.
        """
        title = (urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("title") or [""])[0]
        now = time.time()
        recent = False
        if title:
            db = self._qdb()
            row = db.execute("select at from fetched where title = ?", (title,)).fetchone()
            recent = bool(row and now - row[0] < self.COOLDOWN)
            if not recent:
                db.execute("insert into queue values (?, 9, '사용자 요청', ?) "
                           "on conflict(title) do update set priority = 9", (title, now))
                db.commit()
            db.close()
        back = "/w/" + urllib.parse.quote(title)
        if recent:
            msg = ("<b>최신 버전입니다.</b><br><small>24시간 안에 나무위키에서 확인한 문서입니다"
                   "(서버 부담을 줄이려고 하루에 한 번만 받습니다).</small>")
            script = f"setTimeout(function(){{location.replace({json.dumps(back)})}},2000);"
        else:
            msg = "나무위키에서 최신판을 확인하는 중입니다…"
            script = (f"var t={json.dumps(title)},since={now},n=0,m=document.getElementById('kit-msg');"
                      f"function go(){{location.replace({json.dumps(back)})}}"
                      "function poll(){fetch('/_kit/refresh_status?title='+encodeURIComponent(t)+'&since='+since)"
                      ".then(function(r){return r.json()}).then(function(s){"
                      "if(s.done){m.innerHTML=s.changed?'<b>최신판으로 갱신했습니다.</b>':'<b>최신 버전입니다.</b>';setTimeout(go,1500)}"
                      "else if(++n>45){go()}else{setTimeout(poll,2000)}"
                      "}).catch(function(){setTimeout(poll,2000)})}poll();")
        page = ('<meta charset="utf-8"><div style="font-size:16px;padding:24px;line-height:1.7">'
                f'「{html.escape(title)}」<br><span id="kit-msg">{msg}</span><br><br>'
                f'<a href="{back}">← 기다리지 않고 문서로 돌아가기</a></div><script>{script}</script>').encode("utf-8")
        self._send_bytes(page, "text/html; charset=utf-8")

    def _suggest(self):
        """검색창 자동완성: 입력한 글자로 시작하는 문서 제목 10개(제목 색인으로 바로 찾음)."""
        q = (urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("q") or [""])[0].strip()
        out = []
        if q:
            try:
                path = os.path.join(os.path.dirname(os.path.abspath(self.queue_db)), "data.db")
                db = sqlite3.connect(pathlib.Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
                out = [r[0] for r in db.execute(
                    "select title from data where title >= ? and title < ? order by title limit 10",
                    (q, q + "\U0010ffff"))]
                db.close()
            except sqlite3.Error:
                out = []
        out = [("분류:" + t[9:]) if t.startswith("category:") else t for t in out]
        self._send_bytes(json.dumps(out, ensure_ascii=False).encode("utf-8"), "application/json")

    def _refresh_status(self):
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        title = (q.get("title") or [""])[0]
        since = float((q.get("since") or ["0"])[0] or 0)
        db = self._qdb()
        db.execute("create table if not exists result (title text primary key, at real, changed int)")
        row = db.execute("select at from fetched where title = ?", (title,)).fetchone()
        res = db.execute("select changed from result where title = ?", (title,)).fetchone()
        db.close()
        done = bool(row and row[0] >= since)
        self._send_bytes(json.dumps({"done": done, "changed": bool(done and res and res[0])}).encode(),
                         "application/json")

    def log_message(self, *args):
        pass

    def _serve_cdn(self):
        rel = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path[len("/_kit/cdn/"):])
        path = os.path.normpath(os.path.join(self.cdn_dir, rel))
        if not path.startswith(os.path.normpath(self.cdn_dir)) or not os.path.isfile(path):
            self.send_error(404)
            return
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(path)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "max-age=86400")
        self.end_headers()
        self.wfile.write(data)

    def _hub(self):
        """중계소(/_hub/, hub.py 참고): 유어위키들이 보낸 서명된 문서 묶음을 모아 나눠 준다. JSON 만 주고받는다."""
        import hub
        u = urllib.parse.urlsplit(self.path)
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > 64 << 20:
            self.send_error(400 if length < 0 else 413)
            return
        body = self.rfile.read(length) if length else b""
        code, obj = hub.handle(hub.db_path_for(self.queue_db), self.command, u.path,
                               urllib.parse.parse_qs(u.query), body)
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _proxy(self):
        if self.path.startswith("/_hub/") and self.hub and self.queue_db:
            return self._hub()
        if self.path.startswith("/_kit/cdn/"):
            return self._serve_cdn()
        if self.path.startswith("/_kit/suggest") and self.queue_db:
            return self._suggest()
        if self.path.startswith("/_kit/refresh_status") and self.queue_db:
            return self._refresh_status()
        if self.path.startswith("/_kit/refresh") and self.queue_db:
            return self._refresh()
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0:
            self.send_error(400)
            return
        body = self.rfile.read(length) if length else None
        headers = {k: v for k, v in self.headers.items()
                   if k.lower() not in ("host", "accept-encoding", "connection")}
        headers["Host"] = "%s:%d" % self.upstream
        headers["Accept-Encoding"] = "identity"
        conn = http.client.HTTPConnection(*self.upstream, timeout=300)
        try:
            conn.request(self.command, self.path, body=body, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
        except OSError:
            # 상태 줄에는 한글을 넣을 수 없다(latin-1). 안내는 본문으로 보낸다.
            page = ('<meta charset="utf-8"><meta http-equiv="refresh" content="10">'
                    '<div style="font-size:16px;padding:24px;line-height:1.7">'
                    '위키 엔진이 아직 준비 중입니다.<br>처음 켤 때나 오랜만에 켤 때는 문서가 많아 몇 분 걸릴 수 있습니다.<br>'
                    '10초마다 자동으로 다시 시도합니다.</div>').encode("utf-8")
            self.send_response(503, "Service Unavailable")
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)
            return
        finally:
            conn.close()
        ctype = resp.getheader("Content-Type", "")
        if "text/html" in ctype:
            data = rewrite_html(data.decode("utf-8", "replace"))
            if self.queue_db:
                data = add_suggest(refresh_button(hide_missing_images(ext_decorate(data, self.path, self.queue_db)), self.path, self.queue_db))
            data = data.encode("utf-8")
        self.send_response(resp.status, resp.reason)
        for k, v in resp.getheaders():
            kl = k.lower()
            if kl in ("content-length", "transfer-encoding", "connection", "content-encoding"):
                continue
            if kl == "location":
                v = v.replace("%s:%d" % self.upstream, self.headers.get("Host", ""))
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    do_GET = do_POST = do_HEAD = do_PUT = do_DELETE = do_PATCH = _proxy


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("assets_dir")
    ap.add_argument("--listen", default="127.0.0.1:3000")
    ap.add_argument("--upstream", default="127.0.0.1:3001")
    ap.add_argument("--queue-db", default="", help="갱신 대기열(wiki/updater.db). 주면 갱신 단추가 생긴다")
    ap.add_argument("--hub", action="store_true", help="중계소를 연다(/_hub/, 고정 주소가 있는 서버에서만 의미 있음)")
    args = ap.parse_args()
    Handler.hub = args.hub
    if args.hub:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    host, port = args.listen.rsplit(":", 1)
    uhost, uport = args.upstream.rsplit(":", 1)
    Handler.upstream = (uhost, int(uport))
    Handler.queue_db = args.queue_db
    load_ext()
    Handler.cdn_dir = os.path.join(os.path.abspath(args.assets_dir), "cdn")
    with open(os.path.join(args.assets_dir, "icons.json"), encoding="utf-8") as f:
        ICONS.update(json.load(f))
    print(f"중계 서버: http://{host}:{port}  →  openNAMU {uhost}:{uport}", flush=True)
    Server((host, int(port)), Handler).serve_forever()


if __name__ == "__main__":
    main()
