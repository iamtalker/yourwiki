"""중계소(허브): 유어위키들이 나무위키에서 받은 문서를 모아 두고 나눠 주는 곳 (유어위키 1.2, 표준 라이브러리만 사용).

각 유어위키는 중계소로 '보내고' '받기'만 하므로 공개 주소·공유기 설정이 필요 없다.
중계소는 고정 주소(도메인 + HTTPS)가 있는 서버에서 돌린다. Docker 는 HUB=on, 리눅스는 HUB=on bash server/yourwiki.sh start.
중계서버(offline_proxy.py --hub)가 /_hub/ 요청을 이 모듈로 넘긴다. 저장소는 wiki/hub.db.

    POST /_hub/submit   {"node": ID, "items": [[제목, 수정 시각, 해시, 변환기 판, 받은 시각], ...],
                         "docs": {해시: [본문, 넘겨주기]}, "sig": 서명}
    GET  /_hub/changes?since=N  → 서명된 묶음들(받는 쪽이 서명을 직접 검증한다)
    GET  /_hub/doc?sha=X        → 본문(해시로 찾으므로 중계소가 내용을 바꿀 수 없다)

중계소는 내용을 믿지도 판단하지도 않는다. 서명이 맞는 묶음만 받아 그대로 전하고,
어느 ID 를 믿을지는 받는 쪽이 검증 실적으로 스스로 정한다(p2p.py). 중계소가 할 수 있는 나쁜 짓은 '안 전하기'뿐이다.
"""
import json
import os
import re
import sqlite3
import time

import ed25519

APP = "yourwiki-hub"
VERSION = 2
MAX_ITEMS = 500              # 묶음 하나의 최대 문서 수
NODE_HOURLY = 5000           # ID 하나가 한 시간에 보낼 수 있는 문서 수
NEW_NODES_HOURLY = 200       # 처음 보는 ID 가 한 시간에 새로 생길 수 있는 수(ID 대량 생성 막기)
MAX_TEXT = 5 << 20
KEEP_DAYS = 30
PAGE = 50                    # changes 한 번에 주는 묶음 수
MODIFIED_RE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
HEX64 = re.compile(r"[0-9a-f]{64}$")
MAGIC = b"yourwiki-p2p-v2\n"


def canonical(items):
    return MAGIC + json.dumps(items, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def open_db(path):
    db = sqlite3.connect(path, timeout=30)
    db.executescript("""
        create table if not exists batches (seq integer primary key autoincrement, id text unique, node text,
                                            items text, sig text, at real);
        create table if not exists items (seq int, title text, sha text);
        create index if not exists items_sha on items(sha);
        create table if not exists docs (sha text primary key, text text, redirect text);
        create table if not exists nodes (node text primary key, first_seen real);
        create table if not exists quota (node text, hour int, n int, primary key (node, hour));
    """)
    return db


def submit(db, body):
    import hashlib
    try:
        m = json.loads(body)
        node, items, docs, sig = m["node"], m["items"], m.get("docs", {}), bytes.fromhex(m["sig"])
    except (ValueError, KeyError, TypeError):
        return 400, {"error": "형식이 잘못됨"}
    if not isinstance(node, str) or not HEX64.match(node) or not isinstance(items, list) \
            or not isinstance(docs, dict) or not 0 < len(items) <= MAX_ITEMS:
        return 400, {"error": "형식이 잘못됨"}
    if not ed25519.verify(bytes.fromhex(node), canonical(items), sig):
        return 403, {"error": "서명이 맞지 않음"}
    now = time.time()
    future = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now + 9 * 3600 + 600))  # 나무위키 시각(한국 표준시)
    for it in items:
        if not (isinstance(it, list) and len(it) == 5 and isinstance(it[0], str) and it[0]
                and isinstance(it[1], str) and MODIFIED_RE.match(it[1]) and it[1] <= future
                and isinstance(it[2], str) and HEX64.match(it[2]) and isinstance(it[3], str) and len(it[3]) <= 16
                and isinstance(it[4], (int, float)) and it[4] <= now + 600):
            return 400, {"error": "항목이 잘못됨"}
    # 처음 보는 ID 는 중계소 전체에서 한 시간에 NEW_NODES_HOURLY 개까지만 받는다
    if not db.execute("select 1 from nodes where node = ?", (node,)).fetchone():
        recent = db.execute("select count(*) from nodes where first_seen > ?", (now - 3600,)).fetchone()[0]
        if recent >= NEW_NODES_HOURLY:
            return 429, {"error": "새 ID 가 너무 많음. 잠시 뒤 다시"}
        db.execute("insert into nodes values (?, ?)", (node, now))
    hour = int(now // 3600)
    used = (db.execute("select n from quota where node = ? and hour = ?", (node, hour)).fetchone() or [0])[0]
    if used + len(items) > NODE_HOURLY:
        return 429, {"error": "이 ID 의 한 시간 한도를 넘음"}
    # 본문: 해시가 맞는 것만. 이미 있는 본문은 다시 보내지 않아도 된다.
    from p2p import digest
    for it in items:
        sha = it[2]
        if db.execute("select 1 from docs where sha = ?", (sha,)).fetchone():
            continue
        d = docs.get(sha)
        if not (isinstance(d, list) and len(d) == 2 and isinstance(d[0], str) and isinstance(d[1], str)
                and len(d[0]) <= MAX_TEXT and digest(d[0], d[1]) == sha):
            return 400, {"error": f"본문이 없거나 해시가 다름: {it[0]}"}
        db.execute("insert or ignore into docs values (?, ?, ?)", (sha, d[0], d[1]))
    bid = hashlib.sha256(canonical(items) + bytes.fromhex(node)).hexdigest()
    cur = db.execute("insert or ignore into batches (id, node, items, sig, at) values (?, ?, ?, ?, ?)",
                     (bid, node, json.dumps(items, ensure_ascii=False, separators=(",", ":")), sig.hex(), now))
    if cur.rowcount:
        seq = cur.lastrowid
        db.executemany("insert into items values (?, ?, ?)", [(seq, it[0], it[2]) for it in items])
        db.execute("insert into quota values (?, ?, ?) on conflict(node, hour) do update set n = n + ?",
                   (node, hour, len(items), len(items)))
    if now % 3600 < 60:  # 가끔 오래된 것 정리
        prune(db)
    db.commit()
    return 200, {"ok": True, "id": bid}


def prune(db):
    old = time.time() - KEEP_DAYS * 86400
    db.execute("delete from items where seq in (select seq from batches where at < ?)", (old,))
    db.execute("delete from batches where at < ?", (old,))
    db.execute("delete from docs where sha not in (select sha from items)")
    db.execute("delete from quota where hour < ?", (int(time.time() // 3600) - 48,))


def handle(db_path, method, path, query, body):
    """(상태 코드, JSON 객체)."""
    db = open_db(db_path)
    try:
        if path == "/_hub/hello":
            n = db.execute("select count(*) from nodes").fetchone()[0]
            last = db.execute("select max(seq) from batches").fetchone()[0] or 0
            return 200, {"app": APP, "v": VERSION, "nodes": n, "seq": last}
        if path == "/_hub/submit" and method == "POST":
            return submit(db, body)
        if path == "/_hub/changes":
            try:
                since = int(query.get("since", ["0"])[0] or 0)
            except ValueError:
                since = 0
            rows = db.execute("select seq, node, items, sig from batches where seq > ? order by seq limit ?",
                              (since, PAGE)).fetchall()
            return 200, {"batches": [{"seq": s, "node": n, "items": json.loads(i), "sig": g} for s, n, i, g in rows],
                         "next": rows[-1][0] if rows else since, "more": len(rows) == PAGE}
        if path == "/_hub/doc":
            sha = query.get("sha", [""])[0]
            r = db.execute("select text, redirect from docs where sha = ?", (sha,)).fetchone()
            if not r:
                return 404, {"error": "없음"}
            return 200, {"text": r[0], "redirect": r[1]}
        return 404, {"error": "없음"}
    finally:
        db.close()


def db_path_for(queue_db):
    return os.path.join(os.path.dirname(os.path.abspath(queue_db)), "hub.db")
