"""P2P 공유: 유어위키끼리 나무위키에서 받은 문서를 서버 없이 나눠 갖는다 (유어위키 1.2, 표준 라이브러리만 사용).

갱신기는 나무위키 서버 부담 때문에 6초에 1건만 받는다. 참여한 위키가 N곳이면 서로 다른 문서를 받아
나누므로 전체로는 N배 빠르게 따라잡는다(위키마다 나무위키로 가는 요청은 그대로).

구조
- ID: 각 위키는 처음 켤 때 서명 열쇠(Ed25519)를 만들고, 공개 열쇠가 곧 ID 다(wiki/p2p_key.json).
  주소가 아니라 ID 로 구별하므로 주소가 바뀌어도 상관없고, 친구는 ID 로 한 번만 등록하면 된다.
- 서버 없음(기본): 각 위키가 작은 읽기 전용 창구(hub_server.py --read-only, wiki/hub.db)를 열고,
  자기가 나무위키에서 받은 문서를 서명해 거기 둔다. 창구는 P2P 전용 임시 공개 주소(cloudflared)로 연다.
  '내 ID → 지금 창구 주소'는 BitTorrent 공용 연결망(DHT, dht.py)에 서명해 올려 두므로 주소가 바뀌어도 찾아온다.
  서로 모르는 위키끼리는 DHT 의 '유어위키 게시판'(모두가 같은 열쇠로 읽고 쓰는 공용 칸)에 ID 를 적고 읽어 찾는다.
  친구를 적을 필요는 없다(BitTorrent 처럼 켜 두면 서로 찾는다). 올릴 때 아는 다른 ID 도 함께 적어 퍼지게 한다.
  받아서 서명을 확인한 남의 묶음도 내 창구에 두어 다시 나눠 준다(원래 위키가 꺼져 있어도 퍼지도록).
- 중계소(선택): 누군가 고정 주소 서버에 중계소(hub.py)를 띄우면 거기로도 보내고 받는다. 없어도 된다.
- 서명과 해시 때문에 중간에 거친 창구·중계소는 내용을 바꾸지 못한다(할 수 있는 건 '안 전하기'뿐).

누구를 믿나 (사보타주 방지)
- 등급: 친구(선택, 내가 적은 ID) > 검증된 ID(나무위키와 직접 맞춰 본 검증을 5건 넘게 통과, 거짓 0건) > 수습 ID(그 밖).
  모르는 위키에게서도 자동으로 받되, 수습 한도 안에서만 받고 검증을 통과하면 한도가 풀린다.
- 믿음은 '검증을 통과한 실적'으로만 쌓인다. 주소나 ID 를 새로 만들면 실적이 0 으로 돌아가므로,
  차단된 공격자가 새 ID 로 돌아와도 처음부터 다시 정직하게 일해야 한다.
- 수습 ID 들에게서는 '모두 합쳐' 한 시간에 200개까지만 받는다. ID 를 1,000개 만들어도 오염될 수 있는 양은 그대로다.
- 검증: 갱신기가 나무위키 요청의 20%로 P2P 로 받은 문서를 직접 다시 받아 맞춰 본다(수습 ID 의 것부터).
  같은 수정 시각에 내용이 다르면 거짓이 확실하므로 그 ID 를 차단하고, 그 ID 에게서 받은 문서를 모두
  받기 전 내용으로 되돌린 뒤(없던 문서는 지움) 다시 받을 목록에 넣는다. 친구도 예외가 아니다.
- 내 문서와 절반 넘게 다른 내용은 P2P 로 받지 않고 나무위키에서 직접 확인한다(통째로 난수로 바꾼 문서 등).
- 미래 시각, 서명이 맞지 않는 묶음, 변환기 판이 다른 문서(친구 것 제외)는 받지 않는다.

사용:
  python p2p.py <wiki 폴더> --watch [--friend ID ...] [--tunnel-log p2p-tunnel.log | --self-url URL] [--hub URL ...]
  python p2p.py <wiki 폴더> --id        # 내 ID 보기
"""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ed25519  # noqa: E402
import hub as hubmod  # noqa: E402
from hub import canonical  # noqa: E402

UA = "YourWiki-P2P/2 (+https://github.com/iamtalker/yourwiki)"
SYNC_EVERY = 60          # 중계소에서 받아 오는 주기(초)
MAX_BODY = 64 << 20
MAX_TEXT = 5 << 20
PUSH_BATCH = 300
PUSH_BYTES = 8 << 20     # 묶음 하나의 대략 최대 크기(중계소 한도 64MB 보다 한참 작게)
STRIKES = 3              # 나무위키에 없는 문서를 준 수습 ID 는 이만큼 쌓이면 차단
PROBATION = 5            # 검증을 이만큼 통과하면 '검증된 ID'
CAP_NODE = 3000          # 친구·검증된 ID 하나에게서 한 시간에 받는 최대 문서 수
CAP_PROBATION = 200      # 수습 ID 들 '모두 합쳐' 한 시간에 받는 최대 문서 수
BIG_CHANGE = 0.5         # 내 문서와 줄 단위로 이만큼 넘게 다르면 직접 확인
BIG_MIN = 300            # 이보다 짧은 문서는 큰 변경 검사를 하지 않는다
CLOCK_SLACK = 600
AUDIT_SHARE = 0.2        # 갱신기 요청 중 검증에 쓰는 몫
MODIFIED_RE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
HEX64 = re.compile(r"[0-9a-f]{64}$")
FOOTER_RE = re.compile(r"\n\n----\n \* 출처: \[\[https://namu\.wiki/w/.*\Z", re.S)
DHT_EVERY = 1800         # 내 주소를 DHT 에 다시 올리는 주기(항목은 약 2시간 뒤 사라진다)
RESOLVE_EVERY = 1800     # 한 ID 의 주소를 다시 찾는 주기
MAX_RESOLVE = 30         # 한 번에 주소를 찾는 ID 수
PEX_SHARE = 20           # 내 DHT 기록에 함께 적는 다른 ID 수
BOARD_WRITE_EVERY = 1800 # 게시판에 내 ID 를 적는 주기
BOARD_READ_EVERY = 600   # 게시판을 읽는 주기
BOARD_READ_SLOTS = 8     # 한 번에 읽는 칸 수(8칸 모두)
FORGET_MISSES = 8        # 이만큼 연달아 주소를 못 찾은 모르는 ID 는 잊는다(가짜 ID 도배 대비)
FETCH_TRIES = 3          # 본문을 이만큼 못 받으면 새 판이 올라올 때까지 그 사본은 건너뛴다
PEER_FAILS = 10          # 이만큼 연달아 실패하면 주소를 잊고 다시 찾는다
TIER_NAMES = {"friend": "친구", "proven": "검증된 ID", "probation": "수습 ID"}
_conv = None


def log(msg):
    print(time.strftime("[%Y-%m-%d %H:%M:%S] ") + msg, flush=True)


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


def digest(text, redirect):
    return hashlib.sha256(json.dumps([text, redirect or ""], ensure_ascii=False).encode("utf-8")).hexdigest()


def now_str(offset=0):
    """나무위키 시각(한국 표준시)으로 지금. 서버·컨테이너가 UTC 여도 나무위키 수정 시각과 바로 비교할 수 있게."""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() + 9 * 3600 + offset))


# ---------------------------------------------------------------- 내 ID
def load_key(wiki_dir):
    """내 열쇠. 없으면 만든다. 여러 프로그램이 동시에 처음 켜져도 열쇠가 하나만 생기도록 '없을 때만 만들기'로 쓴다."""
    path = os.path.join(wiki_dir, "p2p_key.json")
    for attempt in range(20):
        try:
            secret = bytes.fromhex(json.load(open(path, encoding="utf-8"))["secret"])
            if len(secret) == 32:
                return secret, ed25519.public_key(secret).hex()
            raise ValueError("열쇠 길이가 이상함")
        except FileNotFoundError:
            secret = ed25519.new_secret()
            data = json.dumps({"secret": secret.hex(), "주의": "이 파일은 내 P2P ID 의 비밀 열쇠입니다. 남에게 주지 마세요."},
                              ensure_ascii=False).encode("utf-8")
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                continue  # 다른 프로그램이 방금 만들었다 → 그것을 읽는다
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            return secret, ed25519.public_key(secret).hex()
        except (OSError, ValueError, KeyError):
            if attempt < 10:  # 다른 프로그램이 쓰는 중일 수 있다
                time.sleep(0.1)
                continue
            broken = path + f".broken-{int(time.time())}"
            os.replace(path, broken)
            log(f"P2P 열쇠 파일이 망가져 {broken} 로 옮기고 새로 만듭니다(ID 가 바뀝니다).")
    raise RuntimeError("P2P 열쇠를 만들지 못했습니다")


def my_id(wiki_dir):
    return load_key(wiki_dir)[1]


# ---------------------------------------------------------------- 저장소 (wiki/updater.db)
def init(q):
    q.executescript("""
        create table if not exists queue (title text primary key, priority int, reason text, added real);
        create table if not exists fetched (title text primary key, at real, namu_modified text);
        create table if not exists shared (title text primary key, modified text, text text, redirect text,
                                           sha text, at real, src text);
        create index if not exists shared_at on shared(at);
        create table if not exists nodes (id text primary key, trusted int default 0, audits_ok int default 0,
                                          audits_bad int default 0, banned int default 0, first_seen real);
        create table if not exists hubs (url text primary key, cursor int default 0, pushed real default 0,
                                         last_ok real default 0, fails int default 0);
        create table if not exists hindex (node text, title text, modified text, sha text, conv text, at real,
                                           hub text, primary key (node, title));
        create index if not exists hindex_title on hindex(title);
        create table if not exists p2p_backup (title text primary key, data text, last_edit text, at real, node text);
        create table if not exists peers (node text primary key, url text, cursor int default 0, last_ok real default 0,
                                          fails int default 0, resolved real default 0, misses int default 0);
        create table if not exists meta (k text primary key, v text);
    """)
    for table, col in (("shared", "node text default ''"), ("shared", "conv text default ''"),
                       ("shared", "audited int default 0"), ("shared", "tier text default ''"),
                       ("hindex", "seen real default 0"), ("hindex", "tries int default 0"),
                       ("nodes", "strikes int default 0"), ("peers", "misses int default 0"),
                       ("nodes", "via text default ''"), ("peers", "alt text default ''")):
        try:
            q.execute(f"alter table {table} add column {col}")
        except sqlite3.OperationalError:
            pass  # 이미 있음
    q.execute("create index if not exists hindex_sha on hindex(title, sha, seen)")
    return q


def record(q, title, text, redirect, modified, src, at=None, node="", conv=None, tier=""):
    """나눌 수 있는 문서로 기록한다(갱신기가 나무위키에서 받았을 때 src='namu', P2P 로 받아 반영했을 때 src='p2p')."""
    init(q)
    q.execute("insert or replace into shared (title, modified, text, redirect, sha, at, src, node, conv, audited, tier) "
              "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
              (title, modified or "", text, redirect or "", digest(text, redirect), at or time.time(), src,
               node, conv or conv_id(), 1 if src == "namu" else 0, tier))


def tier_of(trusted, ok, bad):
    if trusted:
        return "friend"
    return "proven" if ok >= PROBATION and not bad else "probation"


def best_copy(q, title, since=0.0):
    """중계소에서 본 이 문서의 사본 중 받을 만한 가장 새것 (node, modified, sha, tier, hub, conv). 없으면 None."""
    rows = q.execute(
        "select i.node, i.modified, i.sha, i.hub, i.conv, n.trusted, n.audits_ok, n.audits_bad from hindex i "
        "join nodes n on n.id = i.node where i.title = ? and i.at >= ? and n.banned = 0 and i.tries < ? "
        "and (n.trusted = 1 or i.conv = ?)", (title, since, FETCH_TRIES, conv_id())).fetchall()
    rank = {"friend": 2, "proven": 1, "probation": 0}
    best = None
    for node, modified, sha, hub, conv, trusted, ok, bad in rows:
        t = tier_of(trusted, ok, bad)
        key = (modified, rank[t])
        if best is None or key > best[0]:
            best = (key, (node, modified, sha, t, hub, conv))
    return best[1] if best else None


def trusted_copy(q, title, since=0.0):
    """갱신기가 '이미 믿을 만한 누가 받았나'를 볼 때: 친구나 검증된 ID 의 사본만."""
    c = best_copy(q, title, since)
    return c if c and c[3] != "probation" else None


# ---------------------------------------------------------------- 통신
def http_json(url, data=None, timeout=60):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(MAX_BODY + 1)
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read(4096).decode("utf-8")).get("error", "")
        except ValueError:
            msg = ""
        raise ValueError(f"HTTP {e.code} {msg}".strip())
    if len(raw) > MAX_BODY:
        raise ValueError("응답이 너무 큽니다")
    return json.loads(raw.decode("utf-8"))


def norm_url(url):
    url = (url or "").strip().rstrip("/")
    u = urllib.parse.urlsplit(url)
    if u.scheme not in ("http", "https") or not u.hostname or u.query or u.fragment:
        return ""
    return f"{u.scheme}://{u.netloc}{u.path}".rstrip("/")


def public_url_ok(url, resolve=True):
    """모르는 ID 가 알려 준 주소는 사설·내부 주소가 아니어야 한다(내 공유기·내부망을 찌르지 않게).
    https 이거나, 직접 연결(direct.py)처럼 http://공인IP:1024 이상 포트 여야 한다.
    (내용은 서명·해시로 확인하므로 http 로 받아도 바꿔치기는 안 된다)"""
    import ipaddress
    import socket
    if os.environ.get("YOURWIKI_P2P_ALLOW_LOCAL"):  # 시험용(한 컴퓨터에서 여러 위키)
        return True
    u = urllib.parse.urlsplit(url)
    if not u.hostname or u.hostname == "localhost":
        return False
    if u.scheme == "http":
        try:
            return ipaddress.ip_address(u.hostname).is_global and (u.port or 80) >= 1024
        except ValueError:
            return False
    if u.scheme != "https":
        return False
    hosts = [u.hostname]
    if resolve:
        try:
            hosts = [ai[4][0] for ai in socket.getaddrinfo(u.hostname, u.port or 443)]
        except OSError:
            return False
    for h in hosts:
        try:
            if not ipaddress.ip_address(h).is_global:
                return False
        except ValueError:
            continue
    return True


def get_meta(q, k, default=""):
    r = q.execute("select v from meta where k = ?", (k,)).fetchone()
    return r[0] if r else default


def set_meta(q, k, v):
    q.execute("insert or replace into meta values (?, ?)", (k, str(v)))


class Worker:
    def __init__(self, wiki_dir, hubs=(), friends=(), self_url="", tunnel_log="", use_dht=True, seeds=(),
                 bootstrap=None, direct=False, window_port=3002):
        self.wiki_dir = wiki_dir
        self.q = init(sqlite3.connect(os.path.join(wiki_dir, "updater.db"), timeout=30))
        self.secret, self.me = load_key(wiki_dir)
        self.synced, self.clock, self.gave_up = {}, 0.0, 0
        self.hubdb = os.path.join(wiki_dir, "hub.db")
        self.self_url, self.tunnel_log = norm_url(self_url), tunnel_log
        self.direct = None
        if direct:  # Cloudflare 없이 공유기 포트 자동 열기(UPnP)·IPv6 로 창구를 직접 연다
            import direct as directmod
            self.direct = directmod.Direct(window_port, self.me, log)
        self.use_dht, self.bootstrap, self._dht = use_dht, bootstrap, None
        for sd in seeds:
            sd = sd.strip().lower()
            if HEX64.match(sd) and sd != self.me:
                self.q.execute("insert or ignore into nodes (id, first_seen) values (?, ?)", (sd, time.time()))
        hubs = [u for u in (norm_url(h) for h in hubs) if u]
        for h in hubs:
            self.q.execute("insert or ignore into hubs (url) values (?)", (h,))
        self.q.execute(f"delete from hubs where url not in ({','.join('?' * len(hubs))})", hubs)  # 목록에서 뺀 중계소
        self.q.execute("update nodes set trusted = 0")
        for f in friends:
            f = f.strip().lower()
            if HEX64.match(f) and f != self.me:
                self.q.execute("insert into nodes (id, trusted, first_seen) values (?, 1, ?) "
                               "on conflict(id) do update set trusted = 1", (f, time.time()))
                if self.q.execute("select banned from nodes where id = ?", (f,)).fetchone()[0]:
                    log(f"경고: 친구 ID {f[:12]}… 는 검증에서 거짓 내용이 확인되어 차단된 상태입니다.")
        self.q.commit()

    # -- 내 창구: 내가 나무위키에서 받은 문서를 서명해 내 hub.db 에 둔다(다른 위키가 가져간다)
    def local_publish(self):
        done = float(get_meta(self.q, "local_pushed", "0") or 0)
        n = 0
        db = hubmod.open_db(self.hubdb)
        try:
            while True:
                rows = self.q.execute("select title, modified, sha, conv, at, text, redirect from shared "
                                      "where src = 'namu' and at > ? and modified != '' order by at limit ?",
                                      (done, PUSH_BATCH)).fetchall()
                if not rows:
                    break
                items = [[t, m, s, c, a] for t, m, s, c, a, _, _ in rows]
                sig = ed25519.sign(self.secret, canonical(items))
                hubmod.store(db, self.me, items, {s: [x, r or ""] for _, _, s, _, _, x, r in rows}, sig)
                db.commit()
                done = rows[-1][4]
                set_meta(self.q, "local_pushed", done)
                self.q.commit()
                n += len(rows)
        finally:
            db.close()
        return n

    def my_url(self):
        """다른 위키가 내 창구를 찾아올 주소(--self-url, 또는 P2P 전용 터널 기록에서)."""
        if self.tunnel_log:
            try:
                for line in open(self.tunnel_log, encoding="utf-8", errors="replace").readlines()[-300:]:
                    m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
                    if m:
                        self.self_url = m.group(0)
            except OSError:
                pass
        return self.self_url

    def my_urls(self):
        """내 창구 주소들(최대 2개): 직접 연결 주소가 있으면 먼저(Cloudflare 를 덜 쓰게), 그다음 터널·고정 주소."""
        out = list(self.direct.refresh()) if self.direct else []
        if self.my_url():
            out.append(self.self_url)
        return list(dict.fromkeys(out))[:2]

    # -- DHT: 내 주소를 올리고, 친구·아는 ID 의 주소를 찾는다
    def dht(self):
        if self._dht is None:
            import dht
            self._dht = dht.DHT(bootstrap=self.bootstrap)
        return self._dht

    def dht_publish(self):
        urls = self.my_urls()
        if not urls or not self.use_dht:
            return False
        url = " ".join(urls)
        if url == get_meta(self.q, "dht_url") and time.time() - float(get_meta(self.q, "dht_at", "0") or 0) < DHT_EVERY:
            return False
        import dht
        known = [r[0] for r in self.q.execute(
            "select p.node from peers p join nodes n on n.id = p.node where n.banned = 0 and p.last_ok > ? "
            "order by n.trusted desc, n.audits_ok desc, random() limit ?", (time.time() - 86400, PEX_SHARE))]
        value = {"u": urls[0], "p": dht.pack_ids(known), "t": int(time.time())}
        if len(urls) > 1:
            value["a"] = urls[1]  # 두 번째 주소: 첫 주소에 닿지 않는 위키는 이쪽으로
        while len(dht.bencode(value)) > 1000 and known:
            known = known[:-1]
            value["p"] = dht.pack_ids(known)
        ok, seq = self.dht().put(self.secret, bytes.fromhex(self.me), value, int(time.time()))
        if ok:
            set_meta(self.q, "dht_url", url)
            set_meta(self.q, "dht_at", time.time())
            self.q.commit()
            log(f"공용 연결망(DHT)에 내 주소를 올렸습니다: {url} (노드 {ok}곳)")
        else:
            log("공용 연결망(DHT)에 닿지 않습니다. UDP 가 막힌 네트워크일 수 있습니다(중계소가 있으면 그쪽으로는 계속 됩니다).")
            set_meta(self.q, "dht_at", time.time() - DHT_EVERY + 300)  # 5분 뒤 다시
            self.q.commit()
        return bool(ok)

    def dht_resolve(self):
        """친구 → 검증된 ID → 그 밖의 아는 ID 순서로, 오래전에 찾은 것부터 주소를 찾는다."""
        if not self.use_dht:
            return 0
        import dht
        now = time.time()
        cand = [r[0] for r in self.q.execute(
            "select n.id from nodes n left join peers p on p.node = n.id where n.banned = 0 and n.id != ? "
            "and coalesce(p.resolved, 0) < ? order by n.trusted desc, n.audits_ok desc, (n.via = 'pex') desc, "
            "coalesce(p.resolved, 0) limit ?", (self.me, now - RESOLVE_EVERY, MAX_RESOLVE))]
        found = 0
        for node in cand:
            self.q.execute("insert into peers (node, resolved) values (?, ?) on conflict(node) do update set "
                           "resolved = excluded.resolved", (node, now))
            try:
                r = self.dht().get(bytes.fromhex(node))
            except (OSError, ValueError) as e:
                log(f"DHT 찾기 실패: {e}")
                break
            if not r or not isinstance(r[1], dict):
                # 못 찾음(아직 안 켰거나 꺼져 있거나 가짜 ID) → 5분, 10분, 20분… 하루까지 늘려 가며 다시 찾는다
                self.q.execute("update peers set misses = misses + 1 where node = ?", (node,))
                misses = self.q.execute("select misses from peers where node = ?", (node,)).fetchone()[0]
                self.q.execute("update peers set resolved = ? where node = ?",
                               (now - RESOLVE_EVERY + min(86400, 300 * 2 ** (misses - 1)), node))
                trusted = self.q.execute("select trusted, audits_ok from nodes where id = ?", (node,)).fetchone()
                if misses >= FORGET_MISSES and not trusted[0] and not trusted[1] and not self.q.execute(
                        "select 1 from hindex where node = ? limit 1", (node,)).fetchone():
                    self.q.execute("delete from peers where node = ?", (node,))
                    self.q.execute("delete from nodes where id = ?", (node,))
                continue
            self.q.execute("update peers set misses = 0 where node = ?", (node,))
            v = r[1]
            trusted = self.q.execute("select trusted from nodes where id = ?", (node,)).fetchone()[0]
            urls = [u for u in (norm_url((v.get(k) or b"").decode("utf-8", "replace")) for k in (b"u", b"a")
                                if isinstance(v.get(k, b""), bytes)) if u and (trusted or public_url_ok(u))]
            if urls:
                url, alt = urls[0], (urls[1] if len(urls) > 1 else "")
                old, old_alt = self.q.execute("select url, alt from peers where node = ?", (node,)).fetchone()
                if old and {old, old_alt or ""} == {url, alt}:
                    url, alt = old, old_alt or ""  # 전에 닿았던 쪽을 계속 먼저
                self.q.execute("update peers set url = ?, alt = ?, fails = 0 where node = ?", (url, alt, node))
                found += 1
                if old != url:
                    log(f"ID {node[:12]}… 의 주소를 찾았습니다: {url}")
            for other in dht.unpack_ids(v.get(b"p") or b"")[:PEX_SHARE]:
                # 실제로 응답한 위키가 '최근에 주고받은 위키'라고 적은 ID 라 게시판의 ID 보다 먼저 찾아본다
                if other != self.me:
                    self.q.execute("insert into nodes (id, first_seen, via) values (?, ?, 'pex') on conflict(id) do update "
                                   "set via = 'pex' where nodes.via != 'pex'", (other, now))
        self.q.commit()
        return found

    def board(self):
        """유어위키 게시판(dht.py): 가끔 아무 칸에 내 ID 를 적고, 다른 칸들을 읽어 모르는 위키를 알아낸다.
        친구를 적지 않아도 켜 두면 서로 찾는다(BitTorrent 처럼)."""
        if not self.use_dht:
            return 0
        import random
        import dht
        now = time.time()
        if self.my_url() and now - float(get_meta(self.q, "board_w", "0") or 0) > BOARD_WRITE_EVERY:
            slot = random.randrange(dht.BOARD_SLOTS)
            ok, _ = dht.board_add(self.dht(), slot, self.me)
            set_meta(self.q, "board_w", now if ok else now - BOARD_WRITE_EVERY + 300)
        new = 0
        if now - float(get_meta(self.q, "board_r", "0") or 0) > BOARD_READ_EVERY:
            for slot in random.sample(range(dht.BOARD_SLOTS), BOARD_READ_SLOTS):
                for other in dht.board_read(self.dht(), slot)[1]:
                    if other != self.me and HEX64.match(other):
                        new += self.q.execute("insert or ignore into nodes (id, first_seen, via) values (?, ?, 'board')",
                                              (other, now)).rowcount
            set_meta(self.q, "board_r", now)
        self.q.commit()
        if new:
            log(f"유어위키 게시판에서 새 위키 {new}곳을 알아냈습니다")
        return new

    # -- 보내기: 내가 나무위키에서 직접 받은 문서만 서명해서 보낸다
    def push(self, hub):
        """묶음은 문서 300개 또는 8MB 까지. 중계소가 영영 받지 않을 묶음(400·413)은 반으로 나눠 다시 보내고,
        문서 하나까지 줄여도 안 되면 그 문서만 건너뛴다. 한도(429)나 연결 문제면 이번에는 그만 보낸다."""
        pushed = self.q.execute("select pushed from hubs where url = ?", (hub,)).fetchone()[0] or 0
        sent, limit = 0, PUSH_BATCH
        while True:
            rows, size = [], 0
            for r in self.q.execute("select title, modified, sha, conv, at, text, redirect from shared "
                                    "where src = 'namu' and at > ? and modified != '' order by at limit ?",
                                    (pushed, limit)):
                n = len(r[5].encode("utf-8")) + 200
                if rows and size + n > PUSH_BYTES:
                    break
                rows.append(r)
                size += n
            if not rows:
                return sent
            items = [[t, m, s, c, a] for t, m, s, c, a, _, _ in rows]
            body = {"node": self.me, "items": items, "sig": ed25519.sign(self.secret, canonical(items)).hex(),
                    "docs": {s: [x, r or ""] for _, _, s, _, _, x, r in rows}}
            try:
                http_json(hub + "/_hub/submit", json.dumps(body, ensure_ascii=False).encode("utf-8"))
            except ValueError as e:
                if not str(e).startswith(("HTTP 400", "HTTP 413")):
                    log(f"중계소 {hub} 보내기 잠시 멈춤: {e}")
                    return sent
                if len(rows) > 1:
                    limit = max(1, len(rows) // 2)
                    continue
                log(f"중계소가 받지 않는 문서라 건너뜀: {rows[0][0]} — {e}")
            pushed = rows[-1][4]
            self.q.execute("update hubs set pushed = ? where url = ?", (pushed, hub))
            self.q.commit()
            sent += len(rows)
            limit = PUSH_BATCH

    # -- 받기: 서명을 직접 확인한 묶음만 목록에 넣는다(중계소를 믿지 않는다)
    def pull(self, hub, peer=None):
        """중계소(hub) 또는 다른 위키의 창구(peer=그 ID)에서 새 묶음을 받는다. 커서는 창구 주인 ID 에 붙여 두므로
        그 위키의 임시 주소가 바뀌어도 처음부터 다시 받지 않는다. 서명을 확인한 묶음은 내 창구에도 둬서 다시 나눈다."""
        if peer:
            cursor = self.q.execute("select cursor from peers where node = ?", (peer,)).fetchone()[0] or 0
        else:
            cursor = self.q.execute("select cursor from hubs where url = ?", (hub,)).fetchone()[0] or 0
        got, bad = 0, 0
        relay = hubmod.open_db(self.hubdb)
        future = now_str(CLOCK_SLACK)
        for _ in range(20):
            r = http_json(f"{hub}/_hub/changes?since={int(cursor)}")
            for b in r.get("batches", []):
                node, items, sig = b.get("node", ""), b.get("items"), b.get("sig", "")
                cursor = max(cursor, int(b.get("seq", cursor)))
                if node == self.me or not isinstance(node, str) or not HEX64.match(node) or not isinstance(items, list):
                    continue
                try:
                    ok = ed25519.verify(bytes.fromhex(node), canonical(items), bytes.fromhex(sig))
                except (ValueError, TypeError):
                    ok = False
                if not ok:
                    bad += 1
                    continue
                self.q.execute("insert or ignore into nodes (id, first_seen) values (?, ?)", (node, time.time()))
                if self.q.execute("select banned from nodes where id = ?", (node,)).fetchone()[0]:
                    continue
                hubmod.store(relay, node, items, {}, sig)  # 대신 전하기(서명은 원래 위키의 것 그대로)
                rows = []
                for it in items:
                    if isinstance(it, list) and len(it) == 5 and isinstance(it[0], str) and isinstance(it[1], str) \
                            and MODIFIED_RE.match(it[1]) and it[1] <= future and isinstance(it[2], str) \
                            and HEX64.match(it[2]) and isinstance(it[3], str) and isinstance(it[4], (int, float)):
                        self.clock = max(time.time(), self.clock + 1e-6)  # 내가 처음 본 순서
                        rows.append((node, it[0], it[1], it[2], it[3][:16], float(it[4]), hub, self.clock))
                self.q.executemany(
                    "insert into hindex (node, title, modified, sha, conv, at, hub, seen) values (?, ?, ?, ?, ?, ?, ?, ?) "
                    "on conflict(node, title) do update set modified = excluded.modified, conv = excluded.conv, "
                    "at = excluded.at, hub = excluded.hub, "
                    "seen = case when hindex.sha = excluded.sha then hindex.seen else excluded.seen end, "
                    "tries = case when hindex.sha = excluded.sha then hindex.tries else 0 end, "
                    "sha = excluded.sha", rows)
                got += len(rows)
            cursor = max(cursor, int(r.get("next", cursor)))
            if peer:
                self.q.execute("update peers set cursor = ? where node = ?", (cursor, peer))
            else:
                self.q.execute("update hubs set cursor = ? where url = ?", (cursor, hub))
            relay.commit()
            self.q.commit()
            if not r.get("more"):
                break
        relay.close()
        if bad:
            log(f"{hub}: 서명이 맞지 않는 묶음 {bad}개를 버렸습니다(중계소가 내용을 바꿨을 수 있음)")
        return got

    def wanted(self, limit=300):
        """중계소에 내 것보다 새 사본이 있는 문서 제목들."""
        return [r[0] for r in self.q.execute(
            "select i.title from hindex i join nodes n on n.id = i.node and n.banned = 0 and i.tries < ? "
            "left join shared s on s.title = i.title left join fetched f on f.title = i.title "
            "group by i.title having max(i.modified) > max(coalesce(s.modified, ''), coalesce(f.namu_modified, '')) "
            "order by random() limit ?", (FETCH_TRIES, limit))]

    def over_cap(self, node, tier):
        since = time.time() - 3600
        if tier == "probation":
            n = self.q.execute("select count(*) from shared where src = 'p2p' and tier = 'probation' and at > ?",
                               (since,)).fetchone()[0]
            return n >= CAP_PROBATION
        n = self.q.execute("select count(*) from shared where src = 'p2p' and node = ? and at > ?",
                           (node, since)).fetchone()[0]
        return n >= CAP_NODE

    def fetch_doc(self, hub, sha):
        hubs = [hub] + [h for h, in self.q.execute(
            "select url from (select url, last_ok from hubs union all select url, last_ok from peers "
            "where url is not null and url != '') where url != ? order by last_ok desc limit 12", (hub,))]
        err = None
        for h in hubs:
            try:
                d = http_json(f"{h}/_hub/doc?sha={sha}", timeout=20)
                text, redirect = d.get("text"), d.get("redirect") or ""
                if isinstance(text, str) and len(text) <= MAX_TEXT and digest(text, redirect) == sha:
                    return text, redirect
                err = ValueError("본문 해시가 다름")
            except (OSError, ValueError, urllib.error.URLError) as e:
                err = e
        raise err or ValueError("본문을 받지 못함")

    def take(self, title):
        import updater  # 같은 폴더
        c = best_copy(self.q, title)
        if not c:
            return False
        node, modified, sha, tier, hub, conv = c
        if modified > now_str(CLOCK_SLACK) or self.over_cap(node, tier):
            return False
        try:
            text, redirect = self.fetch_doc(hub, sha)
        except (OSError, ValueError, urllib.error.URLError):
            # 본문을 가진 창구를 못 찾음. 몇 번 해 보고 안 되면 새 판이 올라올 때까지 이 사본은 건너뛴다.
            self.q.execute("update hindex set tries = tries + 1 where node = ? and title = ? and sha = ?",
                           (node, title, sha))
            self.q.commit()
            if self.q.execute("select tries from hindex where node = ? and title = ?", (node, title)).fetchone()[0] \
                    >= FETCH_TRIES:
                self.gave_up += 1
            return False
        local = current(self.wiki_dir, title)
        if local and not redirect and len(local[0]) >= BIG_MIN and similarity(local[0], text) < 1 - BIG_CHANGE:
            # 큰 변경은 P2P 로 받지 않는다. 나무위키에서 직접 받도록 대기열에 넣는다.
            self.q.execute("insert into queue values (?, 5, '큰 변경 직접 확인', ?) on conflict(title) do update set "
                           "priority = max(priority, 5)", (title, time.time()))
            self.q.execute("delete from hindex where title = ? and sha = ?", (title, sha))
            self.q.commit()
            log(f"큰 변경이라 나무위키에서 직접 확인: {title} ({TIER_NAMES[tier]} {node[:12]}…)")
            return False
        self.q.execute("insert or ignore into p2p_backup values (?, ?, ?, ?, ?)",
                       (title, local[1] if local else None, local[2] if local else None, time.time(), node))
        info = {"redirect": redirect} if redirect else {}
        changed = updater.apply(self.wiki_dir, title, text, info, modified, via=f"ID {node[:12]}…")
        record(self.q, title, text, redirect, modified, "p2p", node=node, tier=tier, conv=conv)
        relay = hubmod.open_db(self.hubdb)
        relay.execute("insert or ignore into docs values (?, ?, ?)", (sha, text, redirect))
        relay.commit()
        relay.close()
        self.q.execute("insert or replace into fetched values (?, ?, ?)", (title, time.time(), modified))
        self.q.execute("delete from queue where title = ?", (title,))
        self.q.commit()
        log(f"{'받음' if changed else '변경 없음'}: {title} (나무위키 수정 {modified}, {TIER_NAMES[tier]} {node[:12]}…)")
        return True

    def round(self):
        now = time.time()
        n = self.local_publish()
        if n:
            log(f"내 창구에 새 문서 {n}개를 올렸습니다")
        try:
            self.dht_publish()
            self.board()
            self.dht_resolve()
        except OSError as e:
            log(f"DHT 오류: {e}")
        mine = set(self.my_urls())
        for node, url, alt in self.q.execute(
                "select p.node, p.url, p.alt from peers p join nodes n on n.id = p.node where n.banned = 0 "
                "and p.url is not null and p.url != '' and p.node != ?", (self.me,)).fetchall():
            if url in mine or now - self.synced.get(node, 0) < SYNC_EVERY:
                continue
            self.synced[node] = now
            try:
                try:
                    got = self.pull(url, peer=node)
                except (OSError, urllib.error.URLError):
                    if not alt:
                        raise
                    got = self.pull(alt, peer=node)  # 첫 주소(예: 공유기 방화벽에 막힌 IPv6)가 안 되면 두 번째로
                    self.q.execute("update peers set url = ?, alt = ? where node = ?", (alt, url, node))
                self.q.execute("update peers set last_ok = ?, fails = 0 where node = ?", (time.time(), node))
                if got:
                    log(f"ID {node[:12]}… 의 창구에서 새 목록 {got}개")
            except (OSError, ValueError, urllib.error.URLError) as e:
                self.q.execute("update peers set fails = fails + 1 where node = ?", (node,))
                fails = self.q.execute("select fails from peers where node = ?", (node,)).fetchone()[0]
                if fails >= PEER_FAILS:  # 꺼졌거나 주소가 바뀌었다 → 잊고 다음에 DHT 로 다시 찾는다
                    self.q.execute("update peers set url = '', resolved = 0 where node = ?", (node,))
                elif fails == 1:
                    log(f"ID {node[:12]}… 의 창구에 닿지 않음: {e}")
            self.q.commit()
        for (hub,) in self.q.execute("select url from hubs").fetchall():
            if now - self.synced.get(hub, 0) < SYNC_EVERY:
                continue
            self.synced[hub] = now
            try:
                try:
                    sent = self.push(hub)
                except (OSError, urllib.error.URLError) as e:  # 보내기가 안 돼도 받기는 한다
                    sent = 0
                    log(f"중계소 {hub} 보내기 실패: {e}")
                got = self.pull(hub)
                self.q.execute("update hubs set last_ok = ?, fails = 0 where url = ?", (time.time(), hub))
                if sent or got:
                    log(f"중계소 {hub}: 보냄 {sent}개 · 새 목록 {got}개")
            except (OSError, ValueError, urllib.error.URLError) as e:
                self.q.execute("update hubs set fails = fails + 1 where url = ?", (hub,))
                n = self.q.execute("select fails from hubs where url = ?", (hub,)).fetchone()[0]
                if n in (1, 5, 50):
                    log(f"중계소 연결 실패({n}번째): {hub} — {e}")
            self.q.commit()
        done, self.gave_up = 0, 0
        for title in self.wanted():
            try:
                done += self.take(title)
            except (OSError, ValueError, urllib.error.URLError) as e:
                log(f"받지 못함: {title} — {e}")
            self.q.commit()
        if self.gave_up:
            log(f"본문을 가진 곳이 없어 {self.gave_up}개 문서를 건너뜀(새 판이 올라오면 다시 받습니다)")
        return done

    def watch(self):
        hubs = [h for h, in self.q.execute("select url from hubs")]
        friends = self.q.execute("select count(*) from nodes where trusted = 1").fetchone()[0]
        log(f"P2P 시작 · 내 ID {self.me} · 친구 {friends}명 · 중계소 {len(hubs)}곳(선택) · "
            f"공용 연결망(DHT) {'사용' if self.use_dht else '끔'}")
        if not self.use_dht and not hubs:
            log("공용 연결망(DHT)도 중계소도 없어 다른 위키를 찾을 수 없습니다.")
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
    """나무위키에서 직접 다시 받아 맞춰 볼 P2P 문서 하나(수습 ID 의 최근 것부터)."""
    init(q)
    r = q.execute("select title from shared where src = 'p2p' and audited = 0 and conv = ? "
                  "order by (tier = 'probation') desc, at desc limit 1", (conv_id(),)).fetchone()
    return r[0] if r else None


def words(text):
    """글자·숫자 낱말만 뽑는다(서식 차이는 무시하고 내용 차이만 보려고)."""
    return re.findall(r"\w+", text or "")


def audit(q, wiki_dir, title, text, redirect, modified):
    """갱신기가 나무위키에서 받은 문서로, 전에 P2P 로 받은 같은 문서를 검증한다.

    'ok' · 'bad'(차단함) · 'unknown'(그 사이 나무위키 문서가 바뀌었거나 변환기 판이 달라 판단할 수 없음) · None(해당 없음)
    - 피어가 말한 수정 시각이 나무위키의 지금 판보다 늦으면 거짓이다(있을 수 없는 판).
    - 같은 수정 시각인데 낱말이 다르면 거짓이다. 서식만 다르면(같은 판을 다른 때에 받아 변환 결과가 조금 다른 경우) 통과.
    - 통과해도, 그 내용을 처음 올린 ID 가 아니면(남의 것을 다시 서명해 올린 경우) 실적으로 쳐 주지 않는다.
    """
    init(q)
    r = q.execute("select node, modified, sha, conv, text, redirect from shared "
                  "where title = ? and src = 'p2p' and audited = 0", (title,)).fetchone()
    if not r:
        return None
    node, pmod, psha, pconv, ptext, predirect = r
    if not modified:  # 나무위키 화면에서 수정 시각을 읽지 못함 → 판단할 수 없음
        q.execute("update shared set audited = 1 where title = ?", (title,))
        return "unknown"
    if pmod > modified:
        ban(q, wiki_dir, node, f"「{title}」 의 수정 시각을 나무위키 지금 판({modified})보다 늦은 {pmod} 로 속임")
        return "bad"
    if pmod < modified or pconv != conv_id():
        q.execute("update shared set audited = 1 where title = ?", (title,))
        q.execute("delete from p2p_backup where title = ?", (title,))
        return "unknown"
    if digest(text, redirect) != psha and (words(text) != words(ptext) or (redirect or "") != (predirect or "")):
        ban(q, wiki_dir, node, f"「{title}」 수정 {modified} 의 내용이 나무위키와 다름")
        return "bad"
    q.execute("update shared set audited = 1 where title = ?", (title,))
    q.execute("delete from p2p_backup where title = ?", (title,))
    first = q.execute("select node from hindex where title = ? and sha = ? order by seen limit 1",
                      (title, psha)).fetchone()
    if first and first[0] == node:  # 처음 올린 ID 만 실적을 얻는다
        q.execute("update nodes set audits_ok = audits_ok + 1 where id = ?", (node,))
    return "ok"


def audit_missing(q, wiki_dir, title, kind):
    """나무위키에서 문서가 없거나(404) 접근 금지(403)일 때. P2P 로 받은 그 문서를 판단한다.

    404: 나무위키에 없는 문서를 준 것이다(그 사이 지워졌을 수도 있다). 받은 내용을 되돌리고,
         수습 ID 가 이런 일을 STRIKES 번 하면 차단한다. 403: 판단할 수 없으므로 검증 대상에서만 뺀다.
    어느 쪽이든 같은 문서를 계속 검증하느라 요청을 낭비하지 않게 audited 로 표시한다.
    """
    init(q)
    r = q.execute("select node, tier from shared where title = ? and src = 'p2p' and audited = 0", (title,)).fetchone()
    if not r:
        return None
    node, tier = r
    if kind != "404":
        q.execute("update shared set audited = 1 where title = ?", (title,))
        q.execute("delete from p2p_backup where title = ?", (title,))
        return "unknown"
    restore(q, wiki_dir, title, node)
    q.execute("delete from shared where title = ?", (title,))
    q.execute("update nodes set strikes = strikes + 1 where id = ?", (node,))
    strikes = q.execute("select strikes from nodes where id = ?", (node,)).fetchone()[0]
    if tier == "probation" and strikes >= STRIKES:
        ban(q, wiki_dir, node, f"나무위키에 없는 문서를 {strikes}번 줌(마지막: 「{title}」)")
        return "bad"
    q.commit()
    return "missing"


def ban(q, wiki_dir, node, why):
    """거짓 내용을 준 ID 를 차단하고, 그 ID 에게서 받은 문서를 모두 되돌린 뒤 다시 받을 목록에 넣는다."""
    q.execute("update nodes set banned = 1, audits_bad = audits_bad + 1 where id = ?", (node,))
    q.execute("delete from hindex where node = ?", (node,))
    titles = [r[0] for r in q.execute("select title from shared where node = ? and src = 'p2p'", (node,))]
    for t in titles:
        restore(q, wiki_dir, t, node)
        q.execute("delete from shared where title = ?", (t,))
        q.execute("delete from fetched where title = ?", (t,))
        q.execute("insert into queue values (?, 3, '사보타주 되돌림', ?) on conflict(title) do update set "
                  "priority = max(priority, 3)", (t, time.time()))
    q.commit()
    log(f"사보타주 감지: ID {node[:12]}… 차단 — {why}. 이 ID 에게서 받은 문서 {len(titles)}개를 되돌리고 다시 받습니다.")


def restore(q, wiki_dir, title, node):
    """P2P 로 받기 전 내용으로 되돌린다(받기 전에 없던 문서는 지운다)."""
    b = q.execute("select data, last_edit from p2p_backup where title = ?", (title,)).fetchone()
    db = sqlite3.connect(os.path.join(wiki_dir, "data.db"), timeout=60)
    wt = wiki_title(title)
    if b and b[0] is not None:
        data, last_edit = b
        db.execute("update data set data = ? where title = ?", (data, wt))
        rev = (db.execute("select max(id + 0) from history where title = ?", (wt,)).fetchone()[0] or 0) + 1
        db.execute("insert into history (id, title, data, date, ip, send, leng, hide, type) "
                   "values (?, ?, ?, ?, '유어위키 P2P', ?, ?, '', '')",
                   (str(rev), wt, data, time.strftime("%Y-%m-%d %H:%M:%S"),
                    f"사보타주로 판단한 ID({node[:12]}…)의 내용을 되돌림", str(len(data))))
        db.execute("delete from data_set where doc_name = ? and set_name in ('last_edit', 'length')", (wt,))
        db.executemany("insert into data_set (doc_name, doc_rev, set_name, set_data) values (?, '', ?, ?)",
                       [(wt, "last_edit", last_edit or ""), (wt, "length", str(len(data)))])
    elif b:
        db.execute("delete from data where title = ?", (wt,))
        db.execute("delete from data_set where doc_name = ?", (wt,))
        db.execute("delete from back where link = ?", (wt,))
    db.commit()
    db.close()
    q.execute("delete from p2p_backup where title = ?", (title,))


def default_seeds():
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return json.load(open(os.path.join(root, "sources.json"), encoding="utf-8")).get("p2p", {}).get("seeds", [])
    except (OSError, ValueError):
        return []


def default_hubs():
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return json.load(open(os.path.join(root, "sources.json"), encoding="utf-8")).get("p2p", {}).get("hubs", [])
    except (OSError, ValueError):
        return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wiki_dir")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--id", action="store_true", help="내 ID 를 보여 주고 끝낸다")
    ap.add_argument("--hub", action="append", default=[], help="중계소 주소(여러 번 줄 수 있음)")
    ap.add_argument("--friend", action="append", default=[], help="친구 ID(여러 번 줄 수 있음)")
    ap.add_argument("--self-url", default="", help="다른 위키가 내 창구를 찾아올 주소(고정 주소가 있을 때)")
    ap.add_argument("--tunnel-log", default="", help="P2P 전용 터널(cloudflared) 기록 파일에서 내 주소를 읽는다")
    ap.add_argument("--no-dht", action="store_true", help="공용 연결망(DHT)을 쓰지 않는다(중계소만)")
    ap.add_argument("--direct", action="store_true",
                    help="Cloudflare 없이 공유기 포트 자동 열기(UPnP)·IPv6 로 창구를 직접 연다(내 공인 IP 가 보임)")
    ap.add_argument("--window-port", type=int, default=3002, help="내 창구(hub_server --read-only) 포트")
    ap.add_argument("--dht-bootstrap", action="append", default=[], help="시험용 DHT 시작 노드 host:port")
    args = ap.parse_args()
    if args.id:
        print(my_id(args.wiki_dir))
        return
    boot = [(h.rsplit(":", 1)[0], int(h.rsplit(":", 1)[1])) for h in args.dht_bootstrap] or None
    w = Worker(args.wiki_dir, args.hub or default_hubs(), args.friend, args.self_url, args.tunnel_log,
               not args.no_dht, default_seeds(), boot, args.direct, args.window_port)
    if not args.watch:
        print(w.round(), "개 받음")
        return
    import signal

    def on_term(*_):
        raise KeyboardInterrupt
    try:
        signal.signal(signal.SIGTERM, on_term)  # 끌 때 공유기에서 빌린 포트를 돌려준다(못 돌려줘도 1시간 뒤 풀림)
    except (ValueError, OSError):
        pass
    try:
        w.watch()
    except KeyboardInterrupt:
        log("사용자가 멈춤")
    finally:
        if w.direct:
            w.direct.close()


if __name__ == "__main__":
    main()
