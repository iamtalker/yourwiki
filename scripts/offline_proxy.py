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
import re
import socketserver
import sqlite3
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


def refresh_button(body, path):
    """문서 화면 위쪽에 '나무위키 최신판으로 갱신' 단추를 붙인다(갱신 대기열이 있을 때만)."""
    if not path.startswith("/w/"):
        return body
    title = urllib.parse.unquote(path[3:].split("?")[0].split("#")[0])
    if not title or title.startswith(("category:", "틀:")):
        return body
    btn = ('<div style="text-align:right;font-size:13px;padding:4px 10px">'
           f'<a href="/_kit/refresh?title={urllib.parse.quote(title)}" rel="nofollow">'
           '🔄 나무위키 최신판으로 갱신</a></div>')
    m = re.search(r"<body[^>]*>", body)
    return body[:m.end()] + btn + body[m.end():] if m else body


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    upstream = ("127.0.0.1", 3001)
    cdn_dir = ""
    queue_db = ""

    def _refresh(self):
        """갱신 단추: 문서를 갱신 대기열 맨 앞(우선순위 9)에 넣는다. 실제로 받는 건 updater.py."""
        title = (urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("title") or [""])[0]
        if title:
            db = sqlite3.connect(self.queue_db, timeout=30)
            db.execute("create table if not exists queue (title text primary key, priority int, reason text, added real)")
            db.execute("insert into queue values (?, 9, '사용자 요청', ?) "
                       "on conflict(title) do update set priority = 9", (title, time.time()))
            db.commit()
            db.close()
        back = "/w/" + urllib.parse.quote(title)
        page = ('<meta charset="utf-8"><div style="font-size:16px;padding:24px;line-height:1.7">'
                f"「{html.escape(title)}」 갱신을 요청했습니다.<br>보통 1분 안에 반영됩니다. 잠시 뒤 문서를 새로고침하세요.<br>"
                "<small>같은 문서는 나무위키 서버 부담을 줄이려고 24시간에 한 번만 받습니다.</small><br><br>"
                f'<a href="{back}">← 문서로 돌아가기</a></div>').encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)

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

    def _proxy(self):
        if self.path.startswith("/_kit/cdn/"):
            return self._serve_cdn()
        if self.path.startswith("/_kit/refresh") and self.queue_db:
            return self._refresh()
        length = int(self.headers.get("Content-Length") or 0)
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
                data = refresh_button(data, self.path)
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
    args = ap.parse_args()
    host, port = args.listen.rsplit(":", 1)
    uhost, uport = args.upstream.rsplit(":", 1)
    Handler.upstream = (uhost, int(uport))
    Handler.queue_db = args.queue_db
    Handler.cdn_dir = os.path.join(os.path.abspath(args.assets_dir), "cdn")
    with open(os.path.join(args.assets_dir, "icons.json"), encoding="utf-8") as f:
        ICONS.update(json.load(f))
    print(f"중계 서버: http://{host}:{port}  →  openNAMU {uhost}:{uport}", flush=True)
    Server((host, int(port)), Handler).serve_forever()


if __name__ == "__main__":
    main()
