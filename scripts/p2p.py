"""P2P 공유: 유어위키끼리 나무위키에서 받은 문서를 중계소를 거쳐 나눠 갖는다 (유어위키 1.2, 표준 라이브러리만 사용).

갱신기는 나무위키 서버 부담 때문에 6초에 1건만 받는다. 참여한 위키가 N곳이면 서로 다른 문서를 받아
나누므로 전체로는 N배 빠르게 따라잡는다(위키마다 나무위키로 가는 요청은 그대로).

구조
- ID: 각 위키는 처음 켤 때 서명 열쇠(Ed25519)를 만들고, 공개 열쇠가 곧 ID 다(wiki/p2p_key.json).
  주소가 아니라 ID 로 구별하므로 주소가 바뀌어도 상관없고, 친구는 ID 로 한 번만 등록하면 된다.
- 중계소(hub.py): 고정 주소가 있는 서버. 각 위키는 자기가 나무위키에서 받은 문서를 서명해서 보내고(push),
  다른 위키가 보낸 것을 받아 온다(pull). 밖으로 나가는 연결만 쓰므로 공개 주소·공유기 설정이 필요 없다.
  중계소는 서명과 해시를 바꿀 수 없어 내용을 조작하지 못한다(할 수 있는 건 '안 전하기'뿐).

누구를 믿나 (사보타주 방지)
- 등급: 친구(내가 적은 ID) > 검증된 ID(나무위키와 직접 맞춰 본 검증을 5건 넘게 통과, 거짓 0건) > 수습 ID(그 밖).
- 믿음은 '검증을 통과한 실적'으로만 쌓인다. 주소나 ID 를 새로 만들면 실적이 0 으로 돌아가므로,
  차단된 공격자가 새 ID 로 돌아와도 처음부터 다시 정직하게 일해야 한다.
- 수습 ID 들에게서는 '모두 합쳐' 한 시간에 200개까지만 받는다. ID 를 1,000개 만들어도 오염될 수 있는 양은 그대로다.
- 검증: 갱신기가 나무위키 요청의 20%로 P2P 로 받은 문서를 직접 다시 받아 맞춰 본다(수습 ID 의 것부터).
  같은 수정 시각에 내용이 다르면 거짓이 확실하므로 그 ID 를 차단하고, 그 ID 에게서 받은 문서를 모두
  받기 전 내용으로 되돌린 뒤(없던 문서는 지움) 다시 받을 목록에 넣는다. 친구도 예외가 아니다.
- 내 문서와 절반 넘게 다른 내용은 P2P 로 받지 않고 나무위키에서 직접 확인한다(통째로 난수로 바꾼 문서 등).
- 미래 시각, 서명이 맞지 않는 묶음, 변환기 판이 다른 문서(친구 것 제외)는 받지 않는다.

사용:
  python p2p.py <wiki 폴더> --watch [--hub URL ...] [--friend ID ...]
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
from hub import canonical  # noqa: E402

UA = "YourWiki-P2P/2 (+https://github.com/iamtalker/yourwiki)"
SYNC_EVERY = 60          # 중계소에서 받아 오는 주기(초)
MAX_BODY = 64 << 20
MAX_TEXT = 5 << 20
PUSH_BATCH = 300
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
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() + offset))


# ---------------------------------------------------------------- 내 ID
def load_key(wiki_dir):
    path = os.path.join(wiki_dir, "p2p_key.json")
    try:
        secret = bytes.fromhex(json.load(open(path, encoding="utf-8"))["secret"])
    except (OSError, ValueError, KeyError):
        secret = ed25519.new_secret()
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"secret": secret.hex(), "주의": "이 파일은 내 P2P ID 의 비밀 열쇠입니다. 남에게 주지 마세요."},
                      f, ensure_ascii=False)
    return secret, ed25519.public_key(secret).hex()


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
    """)
    for col in ("node text default ''", "conv text default ''", "audited int default 0", "tier text default ''"):
        try:
            q.execute(f"alter table shared add column {col}")
        except sqlite3.OperationalError:
            pass  # 이미 있음
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
        "join nodes n on n.id = i.node where i.title = ? and i.at >= ? and n.banned = 0 "
        "and (n.trusted = 1 or i.conv = ?)", (title, since, conv_id())).fetchall()
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
def http_json(url, data=None):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
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


class Worker:
    def __init__(self, wiki_dir, hubs=(), friends=()):
        self.wiki_dir = wiki_dir
        self.q = init(sqlite3.connect(os.path.join(wiki_dir, "updater.db"), timeout=30))
        self.secret, self.me = load_key(wiki_dir)
        self.synced = {}
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

    # -- 보내기: 내가 나무위키에서 직접 받은 문서만 서명해서 보낸다
    def push(self, hub):
        pushed = self.q.execute("select pushed from hubs where url = ?", (hub,)).fetchone()[0] or 0
        sent = 0
        while True:
            rows = self.q.execute("select title, modified, sha, conv, at, text, redirect from shared "
                                  "where src = 'namu' and at > ? and modified != '' order by at limit ?",
                                  (pushed, PUSH_BATCH)).fetchall()
            if not rows:
                return sent
            items = [[t, m, s, c, a] for t, m, s, c, a, _, _ in rows]
            body = {"node": self.me, "items": items, "sig": ed25519.sign(self.secret, canonical(items)).hex(),
                    "docs": {s: [x, r or ""] for _, _, s, _, _, x, r in rows}}
            http_json(hub + "/_hub/submit", json.dumps(body, ensure_ascii=False).encode("utf-8"))
            pushed = rows[-1][4]
            self.q.execute("update hubs set pushed = ? where url = ?", (pushed, hub))
            self.q.commit()
            sent += len(rows)

    # -- 받기: 서명을 직접 확인한 묶음만 목록에 넣는다(중계소를 믿지 않는다)
    def pull(self, hub):
        cursor = self.q.execute("select cursor from hubs where url = ?", (hub,)).fetchone()[0] or 0
        got, bad = 0, 0
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
                rows = []
                for it in items:
                    if isinstance(it, list) and len(it) == 5 and isinstance(it[0], str) and isinstance(it[1], str) \
                            and MODIFIED_RE.match(it[1]) and it[1] <= future and isinstance(it[2], str) \
                            and HEX64.match(it[2]) and isinstance(it[3], str) and isinstance(it[4], (int, float)):
                        rows.append((node, it[0], it[1], it[2], it[3][:16], float(it[4]), hub))
                self.q.executemany("insert or replace into hindex values (?, ?, ?, ?, ?, ?, ?)", rows)
                got += len(rows)
            cursor = max(cursor, int(r.get("next", cursor)))
            self.q.execute("update hubs set cursor = ? where url = ?", (cursor, hub))
            self.q.commit()
            if not r.get("more"):
                break
        if bad:
            log(f"{hub}: 서명이 맞지 않는 묶음 {bad}개를 버렸습니다(중계소가 내용을 바꿨을 수 있음)")
        return got

    def wanted(self, limit=300):
        """중계소에 내 것보다 새 사본이 있는 문서 제목들."""
        return [r[0] for r in self.q.execute(
            "select i.title from hindex i join nodes n on n.id = i.node and n.banned = 0 "
            "left join shared s on s.title = i.title left join fetched f on f.title = i.title "
            "group by i.title having max(i.modified) > max(coalesce(s.modified, ''), coalesce(f.namu_modified, '')) "
            "order by random() limit ?", (limit,))]

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
        hubs = [hub] + [h for h, in self.q.execute("select url from hubs where url != ? order by last_ok desc", (hub,))]
        err = None
        for h in hubs:
            try:
                d = http_json(f"{h}/_hub/doc?sha={sha}")
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
        text, redirect = self.fetch_doc(hub, sha)
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
        self.q.execute("insert or replace into fetched values (?, ?, ?)", (title, time.time(), modified))
        self.q.execute("delete from queue where title = ?", (title,))
        self.q.commit()
        log(f"{'받음' if changed else '변경 없음'}: {title} (나무위키 수정 {modified}, {TIER_NAMES[tier]} {node[:12]}…)")
        return True

    def round(self):
        now = time.time()
        for (hub,) in self.q.execute("select url from hubs").fetchall():
            if now - self.synced.get(hub, 0) < SYNC_EVERY:
                continue
            self.synced[hub] = now
            try:
                sent = self.push(hub)
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
        done = 0
        for title in self.wanted():
            try:
                done += self.take(title)
            except (OSError, ValueError, urllib.error.URLError) as e:
                log(f"받지 못함: {title} — {e}")
            self.q.commit()
        return done

    def watch(self):
        hubs = [h for h, in self.q.execute("select url from hubs")]
        friends = self.q.execute("select count(*) from nodes where trusted = 1").fetchone()[0]
        log(f"P2P 시작 · 내 ID {self.me} · 중계소 {len(hubs)}곳 · 친구 {friends}명")
        if not hubs:
            log("중계소가 없습니다. 관리판에서 중계소 주소를 적어 주세요.")
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


def audit(q, wiki_dir, title, text, redirect, modified):
    """갱신기가 나무위키에서 받은 문서로, 전에 P2P 로 받은 같은 문서를 검증한다.

    'ok' · 'bad'(차단함) · 'unknown'(그 사이 나무위키 문서가 바뀌었거나 변환기 판이 달라 판단할 수 없음) · None(해당 없음)
    """
    init(q)
    r = q.execute("select node, modified, sha, conv from shared where title = ? and src = 'p2p' and audited = 0",
                  (title,)).fetchone()
    if not r:
        return None
    node, pmod, psha, pconv = r
    if pmod != modified or pconv != conv_id():
        q.execute("update shared set audited = 1 where title = ?", (title,))
        q.execute("delete from p2p_backup where title = ?", (title,))
        return "unknown"
    if digest(text, redirect) == psha:
        q.execute("update shared set audited = 1 where title = ?", (title,))
        q.execute("update nodes set audits_ok = audits_ok + 1 where id = ?", (node,))
        q.execute("delete from p2p_backup where title = ?", (title,))
        return "ok"
    ban(q, wiki_dir, node, f"「{title}」 수정 {modified} 의 내용이 나무위키와 다름")
    return "bad"


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
                   (str(rev), wt, data, now_str(), f"사보타주로 판단한 ID({node[:12]}…)의 내용을 되돌림", str(len(data))))
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
    args = ap.parse_args()
    if args.id:
        print(my_id(args.wiki_dir))
        return
    w = Worker(args.wiki_dir, args.hub or default_hubs(), args.friend)
    if not args.watch:
        print(w.round(), "개 받음")
        return
    try:
        w.watch()
    except KeyboardInterrupt:
        log("사용자가 멈춤")


if __name__ == "__main__":
    main()
