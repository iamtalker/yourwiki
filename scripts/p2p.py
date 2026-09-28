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
- 사보타주(일부러 엉터리 내용을 퍼뜨리는 것) 막기:
    1) 검증(감사): 갱신기가 나무위키 요청의 일부(20%)를 P2P 로 받은 문서를 나무위키에서 직접 다시 받아 맞춰 보는 데 쓴다.
       나무위키의 수정 시각이 피어가 말한 것과 같은데 내용이 다르면 거짓말이 확실하므로 그 피어를 차단하고,
       그 피어에게서 받은 문서를 모두 받기 전 내용으로 되돌린 뒤 다시 받을 목록에 넣는다.
    2) 수습 기간: 검증을 5번 통과하기 전의 피어에게서는 한 시간에 100개까지만 받는다(통과 뒤 3,000개).
       대량 오염이 퍼지기 전에 검증에 걸리게 하기 위해서다.
    3) 큰 변경은 직접 확인: 내 문서와 절반 넘게 다른 내용은 P2P 로 받지 않고 나무위키에서 직접 받는다.
    4) 시각 검사: 미래 시각이나 피어가 받은 시각보다 늦은 수정 시각은 거짓으로 보고 버린다.
    5) 변환기 판이 다른 피어의 문서는 검증할 수 없으므로 믿는 피어가 아니면 받지 않는다.
  받기 전 내용은 wiki/updater.db 의 backup 표에 두었다가 검증을 통과하면 지운다.

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
PROBATION = 5            # 검증을 이만큼 통과하기 전까지는 수습 피어
CAP_PROBATION = 100      # 수습 피어에게서 한 시간에 받는 최대 문서 수
CAP = 3000               # 그 밖의 피어
BIG_CHANGE = 0.5         # 내 문서와 줄 단위로 이만큼 넘게 다르면 직접 확인
BIG_MIN = 300            # 이보다 짧은 문서는 큰 변경 검사를 하지 않는다
CLOCK_SLACK = 600        # 시각 검사 여유(초)
AUDIT_SHARE = 0.2        # 갱신기 요청 중 검증에 쓰는 몫
FOOTER_RE = re.compile(r"\n\n----\n \* 출처: \[\[https://namu\.wiki/w/.*\Z", re.S)
_conv = None


def conv_id():
    """변환기(html2namu.py + classmap.json) 판. 같은 HTML 이면 같은 판끼리는 같은 나무마크가 나온다."""
    global _conv
    if _conv is None:
        here = os.path.dirname(os.path.abspath(__file__))
        h = hashlib.sha256()
        for f in (os.path.join(here, "html2namu.py"), os.path.join(here, "..", "assets", "classmap.json")):
            try:
                h.update(open(f, "rb").read().replace(b"\r\n", b"\n"))
            except OSError:
                pass
        _conv = h.hexdigest()[:12]
    return _conv


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
        create table if not exists backup (title text primary key, data text, last_edit text, at real, peer text);
    """)
    for table, col in (("shared", "peer text default ''"), ("shared", "conv text default ''"),
                       ("shared", "audited int default 0"), ("peers", "banned int default 0"),
                       ("peers", "audits_ok int default 0"), ("peers", "audits_bad int default 0"),
                       ("pindex", "conv text default ''")):
        try:
            q.execute(f"alter table {table} add column {col}")
        except sqlite3.OperationalError:
            pass  # 이미 있음
    return q


def digest(text, redirect):
    return hashlib.sha256(json.dumps([text, redirect or ""], ensure_ascii=False).encode("utf-8")).hexdigest()


def record(q, title, text, redirect, modified, src, at=None, peer="", conv=None):
    """나눌 수 있는 문서로 기록한다(갱신기가 나무위키에서 받았을 때, 또는 피어에게서 받아 반영했을 때)."""
    init(q)
    q.execute("insert or replace into shared (title, modified, text, redirect, sha, at, src, peer, conv, audited) "
              "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
              (title, modified or "", text, redirect or "", digest(text, redirect), at or time.time(), src,
               peer, conv or conv_id(), 1 if src == "namu" else 0))


def trusted_copy(q, title, trust_any=False, since=0.0):
    """이 문서를 믿고 받을 수 있는 피어 사본 (peer, modified, sha) 중 가장 새것. 없으면 None.

    since 를 주면 그 시각 뒤에 피어가 갖게 된 사본만 본다(갱신기가 '이미 누가 받았나'를 볼 때).
    """
    fresh = time.time() - FRESH
    rows = q.execute(
        "select i.peer, i.modified, i.sha, i.src, p.trusted from pindex i join peers p on p.url = i.peer "
        "where i.title = ? and i.at >= ? and p.last_ok >= ? and p.banned = 0 "
        "and (p.trusted = 1 or i.conv = ?) order by i.modified desc",
        (title, since, fresh, conv_id())).fetchall()
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
            "select url from peers where verified = 1 and banned = 0 and last_ok >= ? order by last_ok desc limit 50",
            (time.time() - 86400,))]
        return 200, {"app": APP, "v": VERSION, "peers": [p for p in peers if public_url_ok(p, resolve=False)]}
    if path == "/_p2p/changes":
        try:
            since = float(query.get("since", ["0"])[0] or 0)
        except ValueError:
            since = 0.0
        rows = q.execute("select title, modified, sha, at, src, conv from shared where at > ? order by at limit ?",
                         (since, PAGE)).fetchall()
        return 200, {"items": [list(r) for r in rows], "next": rows[-1][3] if rows else since,
                     "more": len(rows) == PAGE}
    if path == "/_p2p/doc":
        title = query.get("title", [""])[0]
        r = q.execute("select title, modified, text, redirect, sha, src, conv from shared where title = ?",
                      (title,)).fetchone()
        if not r:
            return 404, {"error": "없음"}
        return 200, dict(zip(("title", "modified", "text", "redirect", "sha", "src", "conv"), r))
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
                if self.q.execute("select banned from peers where url = ?", (p,)).fetchone()[0]:
                    log(f"경고: 믿는 피어 {p} 는 검증에서 거짓 내용이 확인되어 차단된 상태입니다. 목록에서 빼세요.")
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
                if not isinstance(it, list) or len(it) not in (5, 6):
                    continue
                title, modified, sha, at, src = it[:5]
                conv = it[5] if len(it) == 6 and isinstance(it[5], str) else ""
                if isinstance(title, str) and title and isinstance(sha, str) and len(sha) == 64 \
                        and MODIFIED_RE.fullmatch(modified or "") and isinstance(at, (int, float)) \
                        and modified <= time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(at) + CLOCK_SLACK)):
                    rows.append((url, title, modified, sha, float(at), "namu" if src == "namu" else "p2p", conv[:12]))
            self.q.executemany("insert or replace into pindex (peer, title, modified, sha, at, src, conv) "
                               "values (?, ?, ?, ?, ?, ?, ?)", rows)
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

    def over_cap(self, peer):
        ok, trusted = self.q.execute("select audits_ok, trusted from peers where url = ?", (peer,)).fetchone()
        cap = CAP if trusted or ok >= PROBATION else CAP_PROBATION
        n = self.q.execute("select count(*) from shared where peer = ? and src = 'p2p' and at > ?",
                           (peer, time.time() - 3600)).fetchone()[0]
        return n >= cap

    def take(self, title):
        import updater  # 같은 폴더
        c = trusted_copy(self.q, title, self.trust_any)
        if not c:
            return False
        peer, modified, sha = c
        if modified > time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() + CLOCK_SLACK)):
            self.q.execute("delete from pindex where peer = ? and title = ?", (peer, title))
            return False
        if self.over_cap(peer):
            return False
        try:
            d = fetch_json(f"{peer}/_p2p/doc?title={urllib.parse.quote(title, safe='')}")
        except (OSError, ValueError, urllib.error.URLError) as e:
            self.fail(peer, e)
            raise
        text, redirect = d.get("text", ""), d.get("redirect", "") or ""
        if (d.get("title") != title or not isinstance(text, str) or len(text) > MAX_TEXT
                or d.get("modified") != modified or digest(text, redirect) != sha):
            raise ValueError(f"받은 내용이 목록과 다름: {title}")
        local = current(self.wiki_dir, title)
        if local and not redirect and len(local[0]) >= BIG_MIN and similarity(local[0], text) < 1 - BIG_CHANGE:
            # 큰 변경은 P2P 로 받지 않는다. 나무위키에서 직접 받도록 대기열에 넣는다.
            self.q.execute("insert into queue values (?, 5, '큰 변경 직접 확인', ?) on conflict(title) do update set "
                           "priority = max(priority, 5)", (title, time.time()))
            self.q.execute("delete from pindex where title = ? and sha = ?", (title, sha))
            self.q.commit()
            log(f"큰 변경이라 나무위키에서 직접 확인: {title} ({peer})")
            return False
        if local:
            self.q.execute("insert or ignore into backup values (?, ?, ?, ?, ?)",
                           (title, local[1], local[2], time.time(), peer))
        else:
            self.q.execute("insert or ignore into backup values (?, NULL, NULL, ?, ?)", (title, time.time(), peer))
        info = {"redirect": redirect} if redirect else {}
        changed = updater.apply(self.wiki_dir, title, text, info, modified, via=peer)
        record(self.q, title, text, redirect, modified, "p2p", peer=peer, conv=d.get("conv") or "")
        self.q.execute("insert or replace into fetched values (?, ?, ?)", (title, time.time(), modified))
        self.q.execute("delete from queue where title = ?", (title,))
        self.q.commit()
        log(f"{'받음' if changed else '변경 없음'}: {title} (나무위키 수정 {modified}, {peer})")
        return True

    def round(self):
        now = time.time()
        for url, hello_at in self.q.execute(
                "select url, hello_at from peers where banned = 0 order by random()").fetchall():
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


# ---------------------------------------------------------------- 사보타주 방지: 비교·검증·차단·되돌리기
def wiki_title(t):
    return "category:" + t[3:] if t.startswith("분류:") else t


def current(wiki_dir, title):
    """내 위키의 지금 문서 (출처 고지를 뗀 본문, 원래 data, last_edit). 없으면 None."""
    db = sqlite3.connect(os.path.join(wiki_dir, "data.db"), timeout=60)
    wt = wiki_title(title)
    r = db.execute("select data from data where title = ?", (wt,)).fetchone()
    le = db.execute("select set_data from data_set where doc_name = ? and set_name = 'last_edit'", (wt,)).fetchone()
    db.close()
    if not r or r[0] is None:
        return None
    return FOOTER_RE.sub("", r[0]), r[0], le[0] if le else None


def similarity(a, b):
    """줄 단위로 얼마나 같은지(0~1). 순서는 보지 않는 빠른 추정."""
    la, lb = [x for x in a.split("\n") if x.strip()], [x for x in b.split("\n") if x.strip()]
    if not la or not lb:
        return 1.0 if la == lb else 0.0
    sa = {}
    for x in la:
        sa[x] = sa.get(x, 0) + 1
    common = 0
    for x in lb:
        if sa.get(x):
            sa[x] -= 1
            common += 1
    return common / max(len(la), len(lb))


def audit_candidate(q):
    """나무위키에서 직접 다시 받아 맞춰 볼 P2P 문서 하나(수습 피어의 최근 문서부터)."""
    r = q.execute("select s.title from shared s join peers p on p.url = s.peer "
                  "where s.src = 'p2p' and s.audited = 0 and s.conv = ? "
                  "order by (p.audits_ok >= ?), p.trusted, s.at desc limit 1", (conv_id(), PROBATION)).fetchone()
    return r[0] if r else None


def audit(q, wiki_dir, title, text, redirect, modified):
    """갱신기가 나무위키에서 받은 문서로, 전에 P2P 로 받은 같은 문서를 검증한다.

    'ok' · 'bad'(차단함) · 'unknown'(그 사이 나무위키 문서가 바뀌었거나 변환기 판이 달라 판단할 수 없음) · None(해당 없음)
    """
    init(q)
    r = q.execute("select peer, modified, sha, conv from shared where title = ? and src = 'p2p' and audited = 0",
                  (title,)).fetchone()
    if not r:
        return None
    peer, pmod, psha, pconv = r
    if pmod != modified or pconv != conv_id():
        q.execute("update shared set audited = 1 where title = ?", (title,))
        q.execute("delete from backup where title = ?", (title,))
        return "unknown"
    if digest(text, redirect) == psha:
        q.execute("update shared set audited = 1 where title = ?", (title,))
        q.execute("update peers set audits_ok = audits_ok + 1 where url = ?", (peer,))
        q.execute("delete from backup where title = ?", (title,))
        return "ok"
    ban(q, wiki_dir, peer, f"「{title}」 수정 {modified} 의 내용이 나무위키와 다름")
    return "bad"


def ban(q, wiki_dir, peer, why):
    """거짓 내용을 준 피어를 차단하고, 그 피어에게서 받은 문서를 모두 되돌린 뒤 다시 받을 목록에 넣는다."""
    q.execute("update peers set banned = 1, audits_bad = audits_bad + 1 where url = ?", (peer,))
    q.execute("delete from pindex where peer = ?", (peer,))
    titles = [r[0] for r in q.execute("select title from shared where peer = ? and src = 'p2p'", (peer,))]
    for t in titles:
        restore(q, wiki_dir, t, peer)
        q.execute("delete from shared where title = ?", (t,))
        q.execute("delete from fetched where title = ?", (t,))
        q.execute("insert into queue values (?, 3, '사보타주 되돌림', ?) on conflict(title) do update set "
                  "priority = max(priority, 3)", (t, time.time()))
    q.commit()
    log(f"사보타주 감지: {peer} 차단 — {why}. 이 피어에게서 받은 문서 {len(titles)}개를 되돌리고 다시 받습니다.")


def restore(q, wiki_dir, title, peer):
    """P2P 로 받기 전 내용으로 되돌린다(받기 전에 없던 문서는 지운다)."""
    b = q.execute("select data, last_edit from backup where title = ?", (title,)).fetchone()
    db = sqlite3.connect(os.path.join(wiki_dir, "data.db"), timeout=60)
    wt = wiki_title(title)
    if b and b[0] is not None:
        data, last_edit = b
        db.execute("update data set data = ? where title = ?", (data, wt))
        rev = (db.execute("select max(id + 0) from history where title = ?", (wt,)).fetchone()[0] or 0) + 1
        db.execute("insert into history (id, title, data, date, ip, send, leng, hide, type) "
                   "values (?, ?, ?, ?, '유어위키 P2P', ?, ?, '', '')",
                   (str(rev), wt, data, time.strftime("%Y-%m-%d %H:%M:%S"),
                    f"사보타주로 판단한 피어({peer})의 내용을 되돌림", str(len(data))))
        db.execute("delete from data_set where doc_name = ? and set_name in ('last_edit', 'length')", (wt,))
        db.executemany("insert into data_set (doc_name, doc_rev, set_name, set_data) values (?, '', ?, ?)",
                       [(wt, "last_edit", last_edit or ""), (wt, "length", str(len(data)))])
    elif b:
        db.execute("delete from data where title = ?", (wt,))
        db.execute("delete from data_set where doc_name = ?", (wt,))
        db.execute("delete from back where link = ?", (wt,))
    db.commit()
    db.close()
    q.execute("delete from backup where title = ?", (title,))


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
