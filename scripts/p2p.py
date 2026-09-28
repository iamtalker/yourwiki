"""P2P 공유: 유어위키끼리 나무위키에서 받은 문서를 나눠 갖는다 (유어위키 1.2, 표준 라이브러리만 사용).

갱신기는 나무위키 서버 부담 때문에 6초에 1건만 받는다. 참여한 위키가 N곳이면
서로 다른 문서를 받아 나누므로 전체로는 N배 빠르게 따라잡는다.

- 나눌 것: 갱신기가 나무위키에서 받은 문서(나무마크로 되돌린 본문). 덤프 원본은 모두 갖고 있어 나누지 않는다.
- 통로: 중계 서버의 /_p2p/ 주소(HTTP). 관리판의 [공개하기] 주소나 공유기로 연 주소로 다른 위키가 찾아온다.
    GET  /_p2p/hello                 → {"app": "yourwiki", "v": 1, "peers": [...]}
    POST /_p2p/hello  {"url": 내 주소} → 나를 알린다(상대가 확인한 뒤 목록에 넣는다)
    GET  /_p2p/changes?since=T       → 그 뒤에 새로 갖게 된 문서 목록 [제목, 수정 시각, 해시, 받은 시각, 출처]
    GET  /_p2p/doc?title=X           → 문서 본문
- 믿는 규칙(엉터리 내용이 끼어드는 것을 막기 위해):
    직접 추가한 피어(--peer, 관리판의 '믿는 피어')가 준 문서는 바로 받는다.
    서로 알려 주다 찾은 피어는, 서로 다른 두 곳 이상이 나무위키에서 직접 받은 같은 내용일 때만 받는다.
    (--trust-any 를 주면 아무 피어 한 곳만으로도 받는다. 빠르지만 위험하다.)
  받은 문서는 역사에 어느 피어에서 왔는지 남으므로 되돌릴 수 있다.

사용:
  python p2p.py <wiki 폴더> --watch [--peer URL ...] [--self-url URL | --tunnel-log tunnel.log] [--trust-any]
"""
import argparse
import hashlib
import ipaddress
import json
import os
import random
import re
import socket
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

APP = "yourwiki"
VERSION = 1
UA = "YourWiki-P2P/1 (+https://github.com/iamtalker/yourwiki)"
SYNC_EVERY = 60          # 피어마다 변경 목록을 가져오는 주기(초)
HELLO_EVERY = 600        # 피어 목록을 주고받는 주기(초)
MAX_PEERS = 200
MAX_BODY = 20 << 20      # 응답 하나의 최대 크기
MAX_TEXT = 5 << 20       # 문서 하나의 최대 크기
PAGE = 1000              # 변경 목록 한 번에 주고받는 개수
FRESH = 600              # 이 시간(초) 안에 응답한 피어만 '살아 있다'고 본다
MODIFIED_RE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")


def log(msg):
    print(time.strftime("[%Y-%m-%d %H:%M:%S] ") + msg, flush=True)


# ---------------------------------------------------------------- 저장소 (wiki/updater.db)
def init(q):
    q.executescript("""
        create table if not exists queue (title text primary key, priority int, reason text, added real);
        create table if not exists fetched (title text primary key, at real, namu_modified text);
        create table if not exists shared (title text primary key, modified text, text text, redirect text,
                                           sha text, at real, src text);
        create index if not exists shared_at on shared(at);
        create table if not exists peers (url text primary key, trusted int, added real, last_ok real,
                                          fails int default 0, cursor real default 0, hello_at real default 0,
                                          verified int default 0);
        create table if not exists pindex (peer text, title text, modified text, sha text, at real, src text,
                                           primary key (peer, title));
        create index if not exists pindex_title on pindex(title);
    """)
    return q


def digest(text, redirect):
    return hashlib.sha256(json.dumps([text, redirect or ""], ensure_ascii=False).encode("utf-8")).hexdigest()


def record(q, title, text, redirect, modified, src, at=None):
    """나눌 수 있는 문서로 기록한다(갱신기가 나무위키에서 받았을 때, 또는 피어에게서 받아 반영했을 때)."""
    init(q)
    q.execute("insert or replace into shared values (?, ?, ?, ?, ?, ?, ?)",
              (title, modified or "", text, redirect or "", digest(text, redirect), at or time.time(), src))


def trusted_copy(q, title, trust_any=False, since=0.0):
    """이 문서를 믿고 받을 수 있는 피어 사본 (peer, modified, sha) 중 가장 새것. 없으면 None.

    since 를 주면 그 시각 뒤에 피어가 갖게 된 사본만 본다(갱신기가 '이미 누가 받았나'를 볼 때).
    """
    fresh = time.time() - FRESH
    rows = q.execute(
        "select i.peer, i.modified, i.sha, i.src, p.trusted from pindex i join peers p on p.url = i.peer "
        "where i.title = ? and i.at >= ? and p.last_ok >= ? order by i.modified desc",
        (title, since, fresh)).fetchall()
    best = {}
    for peer, modified, sha, src, trusted in rows:
        k = (modified, sha)
        b = best.setdefault(k, {"trusted": [], "namu": set(), "any": []})
        b["any"].append(peer)
        if trusted:
            b["trusted"].append(peer)
        if src == "namu":
            b["namu"].add(peer)
    for (modified, sha), b in sorted(best.items(), key=lambda kv: kv[0][0], reverse=True):
        if b["trusted"]:
            return random.choice(b["trusted"]), modified, sha
        if len(b["namu"]) >= 2 or (trust_any and b["any"]):
            return random.choice(sorted(b["namu"]) or b["any"]), modified, sha
    return None


# ---------------------------------------------------------------- 중계 서버가 쓰는 응답
def serve(q, method, path, query, body, self_urls=()):
    """/_p2p/ 요청에 대한 (상태 코드, JSON 객체)."""
    init(q)
    if path == "/_p2p/hello":
        if method == "POST":
            try:
                url = norm_url(json.loads(body or b"{}").get("url", ""))
            except (ValueError, AttributeError):
                url = ""
            if url and url not in self_urls and public_url_ok(url, resolve=False):
                n = q.execute("select count(*) from peers").fetchone()[0]
                if n < MAX_PEERS:
                    q.execute("insert or ignore into peers (url, trusted, added, last_ok) values (?, 0, ?, 0)",
                              (url, time.time()))
                    q.commit()
        peers = [r[0] for r in q.execute(
            "select url from peers where verified = 1 and last_ok >= ? order by last_ok desc limit 50",
            (time.time() - 86400,))]
        return 200, {"app": APP, "v": VERSION, "peers": [p for p in peers if public_url_ok(p, resolve=False)]}
    if path == "/_p2p/changes":
        try:
            since = float(query.get("since", ["0"])[0] or 0)
        except ValueError:
            since = 0.0
        rows = q.execute("select title, modified, sha, at, src from shared where at > ? order by at limit ?",
                         (since, PAGE)).fetchall()
        return 200, {"items": [list(r) for r in rows], "next": rows[-1][3] if rows else since,
                     "more": len(rows) == PAGE}
    if path == "/_p2p/doc":
        title = query.get("title", [""])[0]
        r = q.execute("select title, modified, text, redirect, sha, src from shared where title = ?",
                      (title,)).fetchone()
        if not r:
            return 404, {"error": "없음"}
        return 200, dict(zip(("title", "modified", "text", "redirect", "sha", "src"), r))
    return 404, {"error": "없음"}


# ---------------------------------------------------------------- 주소 검사
def norm_url(url):
    url = (url or "").strip().rstrip("/")
    u = urllib.parse.urlsplit(url)
    if u.scheme not in ("http", "https") or not u.hostname or u.path or u.query or u.fragment:
        return ""
    return f"{u.scheme}://{u.netloc}".lower()


def public_url_ok(url, resolve=True):
    """다른 피어가 알려 준 주소는 https 이고 사설·내부 주소가 아니어야 한다(내 공유기·내부망을 찌르지 않게)."""
    u = urllib.parse.urlsplit(url)
    if u.scheme != "https" or not u.hostname or u.hostname == "localhost":
        return False
    hosts = [u.hostname]
    if resolve:
        try:
            hosts = [ai[4][0] for ai in socket.getaddrinfo(u.hostname, u.port or 443)]
        except OSError:
            return False
    for h in hosts:
        try:
            ip = ipaddress.ip_address(h)
        except ValueError:
            continue
        if not ip.is_global:
            return False
    return True


# ---------------------------------------------------------------- 피어와 통신
def fetch_json(url, data=None):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read(MAX_BODY + 1)
    if len(raw) > MAX_BODY:
        raise ValueError("응답이 너무 큽니다")
    return json.loads(raw.decode("utf-8"))


class Worker:
    def __init__(self, wiki_dir, peers=(), self_url="", tunnel_log="", trust_any=False, bootstrap=()):
        self.wiki_dir = wiki_dir
        self.q = init(sqlite3.connect(os.path.join(wiki_dir, "updater.db"), timeout=30))
        self.self_url, self.tunnel_log, self.trust_any = norm_url(self_url), tunnel_log, trust_any
        self.synced = {}
        for p in peers:
            p = norm_url(p)
            if p:
                self.q.execute("insert into peers (url, trusted, added, last_ok, verified) values (?, 1, ?, 0, 1) "
                               "on conflict(url) do update set trusted = 1, verified = 1", (p, time.time()))
        for p in bootstrap:
            p = norm_url(p)
            if p:
                self.q.execute("insert or ignore into peers (url, trusted, added, last_ok) values (?, 0, ?, 0)",
                               (p, time.time()))
        self.q.commit()

    def my_url(self):
        if self.tunnel_log:
            try:
                for line in open(self.tunnel_log, encoding="utf-8", errors="replace").readlines()[-200:]:
                    m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
                    if m:
                        self.self_url = m.group(0)
            except OSError:
                pass
        return self.self_url

    def ok(self, url):
        self.q.execute("update peers set last_ok = ?, fails = 0, verified = 1 where url = ?", (time.time(), url))

    def fail(self, url, why):
        self.q.execute("update peers set fails = fails + 1 where url = ?", (url,))
        n, trusted = self.q.execute("select fails, trusted from peers where url = ?", (url,)).fetchone()
        if n >= 20 and not trusted:  # 오래 응답이 없는 주소(임시 주소가 바뀐 경우 등)는 잊는다
            self.q.execute("delete from peers where url = ?", (url,))
            self.q.execute("delete from pindex where peer = ?", (url,))
            log(f"피어 삭제(응답 없음): {url}")
        elif n in (1, 5):
            log(f"피어 응답 없음({n}번째): {url} — {why}")

    def safe(self, url):
        trusted = self.q.execute("select trusted from peers where url = ?", (url,)).fetchone()
        return bool(trusted and trusted[0]) or public_url_ok(url)

    def hello(self, url):
        me = self.my_url()
        data = json.dumps({"url": me}).encode() if me else None
        r = fetch_json(url + "/_p2p/hello", data)
        if r.get("app") != APP:
            raise ValueError("유어위키가 아닙니다")
        self.ok(url)
        for p in r.get("peers", [])[:50]:
            p = norm_url(p)
            if p and p != me and public_url_ok(p, resolve=False):
                if self.q.execute("select count(*) from peers").fetchone()[0] < MAX_PEERS:
                    self.q.execute("insert or ignore into peers (url, trusted, added, last_ok) values (?, 0, ?, 0)",
                                   (p, time.time()))
        self.q.execute("update peers set hello_at = ? where url = ?", (time.time(), url))

    def sync(self, url):
        cursor = self.q.execute("select cursor from peers where url = ?", (url,)).fetchone()[0] or 0
        got = 0
        for _ in range(20):
            r = fetch_json(f"{url}/_p2p/changes?since={cursor!r}")
            rows = []
            for it in r.get("items", []):
                if len(it) != 5:
                    continue
                title, modified, sha, at, src = it
                if isinstance(title, str) and title and isinstance(sha, str) and len(sha) == 64 \
                        and MODIFIED_RE.fullmatch(modified or "") and isinstance(at, (int, float)):
                    rows.append((url, title, modified, sha, float(at), "namu" if src == "namu" else "p2p"))
            self.q.executemany("insert or replace into pindex values (?, ?, ?, ?, ?, ?)", rows)
            got += len(rows)
            cursor = float(r.get("next", cursor))
            self.q.execute("update peers set cursor = ? where url = ?", (cursor, url))
            if not r.get("more"):
                break
        self.ok(url)
        return got

    def wanted(self, limit=300):
        """피어가 가진 것 중 내 것보다 새 문서 제목들."""
        return [r[0] for r in self.q.execute(
            "select i.title from pindex i left join shared s on s.title = i.title "
            "left join fetched f on f.title = i.title "
            "group by i.title having max(i.modified) > max(coalesce(s.modified, ''), coalesce(f.namu_modified, '')) "
            "order by random() limit ?", (limit,))]

    def take(self, title):
        import updater  # 같은 폴더
        c = trusted_copy(self.q, title, self.trust_any)
        if not c:
            return False
        peer, modified, sha = c
        try:
            d = fetch_json(f"{peer}/_p2p/doc?title={urllib.parse.quote(title, safe='')}")
        except (OSError, ValueError, urllib.error.URLError) as e:
            self.fail(peer, e)
            raise
        text, redirect = d.get("text", ""), d.get("redirect", "") or ""
        if (d.get("title") != title or not isinstance(text, str) or len(text) > MAX_TEXT
                or d.get("modified") != modified or digest(text, redirect) != sha):
            raise ValueError(f"받은 내용이 목록과 다름: {title}")
        info = {"redirect": redirect} if redirect else {}
        changed = updater.apply(self.wiki_dir, title, text, info, modified, via=peer)
        record(self.q, title, text, redirect, modified, "p2p")
        self.q.execute("insert or replace into fetched values (?, ?, ?)", (title, time.time(), modified))
        self.q.execute("delete from queue where title = ?", (title,))
        self.q.commit()
        log(f"{'받음' if changed else '변경 없음'}: {title} (나무위키 수정 {modified}, {peer})")
        return True

    def round(self):
        now = time.time()
        for url, hello_at in self.q.execute("select url, hello_at from peers order by random()").fetchall():
            if url == self.my_url() or now - self.synced.get(url, 0) < SYNC_EVERY:
                continue
            if not self.safe(url):
                self.q.execute("delete from peers where url = ?", (url,))
                continue
            self.synced[url] = now
            try:
                if now - (hello_at or 0) > HELLO_EVERY:
                    self.hello(url)
                n = self.sync(url)
                if n:
                    log(f"{url}: 새 문서 {n}개 목록 받음")
            except (OSError, ValueError, urllib.error.URLError) as e:
                self.fail(url, e)
            self.q.commit()
        done = 0
        for title in self.wanted():
            try:
                done += self.take(title)
            except (OSError, ValueError, urllib.error.URLError) as e:
                log(f"받지 못함: {title} — {e}")
            self.q.commit()
        return done

    def watch(self):
        n = self.q.execute("select count(*) from peers").fetchone()[0]
        log(f"P2P 시작 · 아는 피어 {n}곳 · 내 공유 주소 {self.my_url() or '(없음: 받기만 합니다)'}")
        while True:
            done = self.round()
            time.sleep(5 if done else SYNC_EVERY)


def bootstrap_peers():
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return json.load(open(os.path.join(root, "sources.json"), encoding="utf-8")).get("p2p", {}).get("bootstrap", [])
    except (OSError, ValueError):
        return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wiki_dir")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--peer", action="append", default=[], help="믿는 피어 주소(여러 번 줄 수 있음)")
    ap.add_argument("--self-url", default="", help="다른 위키가 나를 찾아올 주소")
    ap.add_argument("--tunnel-log", default="", help="관리판의 공개 주소(cloudflared 로그)에서 내 주소를 읽는다")
    ap.add_argument("--trust-any", action="store_true", help="찾은 피어 한 곳만으로도 받는다(빠르지만 위험)")
    args = ap.parse_args()
    w = Worker(args.wiki_dir, args.peer, args.self_url, args.tunnel_log, args.trust_any, bootstrap_peers())
    if not args.watch:
        print(w.round(), "개 받음")
        return
    try:
        w.watch()
    except KeyboardInterrupt:
        log("사용자가 멈춤")


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    main()
