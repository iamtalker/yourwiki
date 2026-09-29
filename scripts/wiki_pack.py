"""유어위키(openNAMU) 형식으로 내보내기·가져오기 (유어위키 1.2, 표준 라이브러리만 사용).

내보낸 파일은 openNAMU 의 data.db 와 같은 SQLite 형식이다(문서 표 data·history·data_set·back 만).
사용자 계정·접속 기록·토론 같은 표는 넣지 않는다(비밀번호 해시·IP 가 있으므로).
'전체'로 내보낸 파일은 새 openNAMU 의 data.db 로 그대로 써도 된다(없는 표는 openNAMU 가 켤 때 만든다).

범위
- all     : 모든 문서
- changed : 설치한 판(예: 2026-08 덤프) 이후 바뀐 문서만 — 갱신기·P2P·직접 편집으로 생긴 판.
            덤프에서 온 판(기록자 '나무위키 덤프' 등)과 기준일 이전 판은 빼고, 그 뒤의 판만 담는다.

가져오기는 openNAMU 형식 파일(유어위키가 내보낸 것, 또는 다른 openNAMU 의 data.db)을 받는다.
- 문서마다 내 쪽 마지막 판보다 새 판만 역사 뒤에 이어 붙이고, 본문을 그 파일의 최신 본문으로 바꾼다.
- 내 쪽이 같거나 더 새로우면 건너뛴다(덮어쓰지 않는다). 지우기는 옮기지 않는다.
- 가져온 판은 역사의 '편집 요약' 앞에 [가져옴 파일이름] 을 붙여 어디서 왔는지 남긴다.
- 위키 엔진이 꺼져 있을 때만 한다(켜진 채 DB 를 크게 바꾸면 엔진이 꼬일 수 있다).

    python wiki_pack.py export WIKI_DIR [--range all|changed] [--out 파일]
    python wiki_pack.py import WIKI_DIR 파일 [--range all|changed]
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time

DUMP_IPS = ("나무위키 덤프", "알파위키 덤프", "유어위키 키트")
TABLES = ("data", "history", "data_set", "back")
FORMAT = "yourwiki-opennamu-1"
NOTICE = ("이 파일의 문서 텍스트는 CC BY-NC-SA 2.0 KR 입니다. 상업적 이용은 금지됩니다. "
          "원 문서 주소·기여자·라이선스 고지를 지우지 마세요. 저작권은 각 문서의 기여자에게 있습니다.")
CAT_RE = re.compile(r"\[\[분류:([^\]|#]+)")
NAMU_IPS = ("유어위키 갱신기", "유어위키 P2P")   # '나무위키 최신판 그대로' 라고 주장하는 판(검증할 수 있음)
NAMU_MOD_RE = re.compile(r"수정 (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
REDIRECT_RE = re.compile(r"#(?:redirect|넘겨주기) ([^\n]+)", re.I)
DEFAULT_CUTOFF = "2026-08-01"


def cutoff(wiki_dir):
    """'이후 바뀐 것' 의 기준일 = 설치한 덤프의 날짜(wiki/edition.json). 모르면 2026-08-01."""
    try:
        d = json.load(open(os.path.join(wiki_dir, "edition.json"), encoding="utf-8-sig")).get("date", "")
        if re.match(r"\d{4}-\d{2}-\d{2}", d or ""):
            return d[:10]
    except (OSError, ValueError):
        pass
    return DEFAULT_CUTOFF


def changed_where(alias=""):
    """history 에서 '기준일 이후, 덤프가 아닌 판' 을 고르는 조건(자리표시자: 기준일)."""
    a = alias + "." if alias else ""
    return f"{a}date >= ? and {a}ip not in ({','.join('?' * len(DUMP_IPS))})"


def changed_args(cut):
    return (cut,) + DUMP_IPS


def fmt_secs(sec):
    sec = int(sec)
    if sec < 60:
        return f"{sec}초"
    if sec < 3600:
        return f"{sec // 60}분"
    return f"{sec // 3600}시간 {sec % 3600 // 60}분"


class Progress:
    def __init__(self, total):
        self.total, self.t0, self.last = max(total, 1), time.time(), time.time()

    def tick(self, n, force=False):
        now = time.time()
        if not force and now - self.last < 10:
            return
        self.last = now
        rate = n / max(now - self.t0, 1e-6)
        left = (self.total - n) / rate if rate else 0
        print(f"진행 {n:,}/{self.total:,} ({min(100, n * 100 // self.total)}%) · 초당 {rate:,.0f}개 · "
              f"남은 시간 약 {fmt_secs(left)}", flush=True)


def ro(path):
    return sqlite3.connect(f"file:{os.path.abspath(path)}?mode=ro", uri=True, timeout=60)


# ---------------------------------------------------------------- 내보내기
def export(wiki_dir, out, rng="all"):
    src_path = os.path.join(wiki_dir, "data.db")
    cut = cutoff(wiki_dir)
    part = out + ".part"
    if os.path.exists(part):
        os.remove(part)
    src = ro(src_path)
    dst = sqlite3.connect(part)
    dst.execute("pragma journal_mode = off")
    dst.execute("pragma synchronous = off")
    for t in TABLES:
        sql = src.execute("select sql from sqlite_master where type = 'table' and name = ?", (t,)).fetchone()
        if not sql:
            raise SystemExit(f"위키 DB 에 {t} 표가 없습니다(openNAMU 위키가 맞나요?)")
        dst.execute(sql[0])
    dst.execute("create table yourwiki_pack (k text primary key, v text)")
    dst.commit()
    src.close()
    dst.execute("attach database ? as s", (f"file:{os.path.abspath(src_path)}?mode=ro",))

    if rng == "changed":
        dst.execute("create temp table pick (title text primary key)")
        dst.execute(f"insert or ignore into pick select distinct title from s.history where {changed_where()}",
                    changed_args(cut))
        total = dst.execute("select count(*) from pick").fetchone()[0]
        print(f"{cut} 이후 바뀐 문서 {total:,}개를 내보냅니다", flush=True)
        dst.execute("insert into data select * from s.data where title in (select title from pick)")
        dst.execute(f"insert into history select * from s.history where title in (select title from pick) "
                    f"and {changed_where()}", changed_args(cut))
        dst.execute("insert into data_set select * from s.data_set where doc_name in (select title from pick)")
        dst.execute("insert into back select * from s.back where link in (select title from pick)")
        n = total
    else:
        total = dst.execute("select count(*) from s.data").fetchone()[0]
        print(f"모든 문서 {total:,}개를 내보냅니다", flush=True)
        prog, n = Progress(total), 0
        # data 는 진행을 보여 주며 나눠 옮긴다. 나머지 표도 같은 방식.
        for t in TABLES:
            lo, hi = dst.execute(f"select coalesce(min(rowid), 0), coalesce(max(rowid), -1) from s.{t}").fetchone()
            step = 50000
            for a in range(lo, hi + 1, step):
                dst.execute(f"insert into {t} select * from s.{t} where rowid >= ? and rowid < ?", (a, a + step))
                dst.commit()
                if t == "data":
                    n = min(total, n + step)
                    prog.tick(n)
            print(f"  {t} 표 옮김", flush=True)
    dst.executemany("insert into yourwiki_pack values (?, ?)", [
        ("format", FORMAT), ("range", rng), ("cutoff", cut if rng == "changed" else ""),
        ("created", time.strftime("%Y-%m-%d %H:%M:%S")), ("docs", str(n)), ("license", NOTICE)])
    dst.commit()
    dst.execute("detach database s")
    # 가져오는 쪽이 빨리 찾도록 openNAMU 와 같은 색인
    dst.execute("create index if not exists history_title_id_index on history (title, id)")
    dst.execute("create index if not exists data_title_index on data (title)")
    dst.execute("create index if not exists data_set_document_index on data_set (doc_name, set_name, doc_rev)")
    dst.execute("create index if not exists back_link_type_index on back (link, type)")
    dst.commit()
    dst.close()
    os.replace(part, out)
    with open(re.sub(r"\.db$", "", out) + "-라이선스.txt", "w", encoding="utf-8") as f:
        f.write(NOTICE + "\n\n형식: openNAMU data.db 와 같은 SQLite(문서 표 data·history·data_set·back 만).\n"
                "다른 유어위키에서는 관리판의 '가져오기'로 넣습니다(import 폴더에 이 파일을 두세요).\n"
                + ("범위: 전체. 새 openNAMU 의 data.db 로 그대로 써도 됩니다.\n" if rng == "all"
                   else f"범위: {cut} 이후 바뀐 문서만(그 뒤의 판만 담김).\n"))
    return n


# ---------------------------------------------------------------- 가져오기
def _has_index(db, table, first_col):
    for (name,) in db.execute("select name from sqlite_master where type = 'index' and tbl_name = ?", (table,)):
        cols = [r[2] for r in db.execute(f"pragma index_info('{name}')")]
        if cols and cols[0] == first_col:
            return True
    return False


def import_pack(wiki_dir, path, rng="all"):
    name = os.path.basename(path)
    src = ro(path)
    tables = {r[0] for r in src.execute("select name from sqlite_master where type = 'table'")}
    if not {"data", "history"} <= tables:
        raise SystemExit("openNAMU 형식이 아닙니다(data·history 표가 없음)")
    meta = dict(src.execute("select k, v from yourwiki_pack").fetchall()) if "yourwiki_pack" in tables else {}
    if meta:
        print(f"유어위키 묶음: 범위 {meta.get('range')} · 문서 {meta.get('docs')}개 · 만든 때 {meta.get('created')}", flush=True)
    if not _has_index(src, "history", "title"):
        print("주의: 이 파일에는 역사 색인이 없어 가져오기가 느릴 수 있습니다", flush=True)
    cut = cutoff(wiki_dir)
    if rng == "changed":
        q = (f"select distinct title from history where {changed_where()}", changed_args(cut))
        total = src.execute(f"select count(*) from ({q[0]})", q[1]).fetchone()[0]
        print(f"{cut} 이후 바뀐 문서 {total:,}개를 살펴봅니다", flush=True)
    else:
        q = ("select title from data", ())
        total = src.execute("select count(*) from data").fetchone()[0]
        print(f"문서 {total:,}개를 살펴봅니다", flush=True)
    titles = (t for (t,) in ro(path).execute(*q))  # 제목 목록은 따로 연결해 흘려 읽는다(180만 개를 메모리에 올리지 않게)

    db = sqlite3.connect(os.path.join(wiki_dir, "data.db"), timeout=60)
    # 검증: '나무위키 최신판 그대로' 라고 적힌 판은 P2P 처럼 갱신기가 나무위키와 맞춰 본다(p2p.audit).
    # 거짓이 하나라도 확인되면 이 파일에서 가져온 문서를 모두 되돌린다. 그래서 바꾸기 전 내용을 p2p_backup 에 남긴다.
    import p2p
    uq = p2p.init(sqlite3.connect(os.path.join(wiki_dir, "updater.db"), timeout=60))
    node = "file:" + name
    uq.execute("delete from meta where k in (?, ?)", ("bad:" + node, "strikes:" + node))  # 같은 이름으로 다시 넣으면 새로 셈
    prog = Progress(total)
    added = skipped = revs = checkable = 0
    tag = f"[가져옴 {name}] "
    for i, title in enumerate(titles, 1):
        where, args = ("title = ?", (title,))
        if rng == "changed":
            where, args = f"title = ? and {changed_where()}", (title,) + changed_args(cut)
        rows = src.execute(f"select id, data, date, ip, send, leng, hide from history where {where} "
                           "order by id + 0", args).fetchall()
        cur = src.execute("select data from data where title = ?", (title,)).fetchone()
        body = cur[0] if cur else (rows[-1][1] if rows else None)
        if body is None or not rows:
            skipped += 1
            prog.tick(i)
            continue
        mine = db.execute("select data from data where title = ?", (title,)).fetchone()
        last = db.execute("select max(date) from history where title = ?", (title,)).fetchone()[0] or ""
        new = [r for r in rows if (r[2] or "") > last]
        if not new or (mine and mine[0] == body):
            skipped += 1  # 내 쪽이 같거나 더 새롭다
            prog.tick(i)
            continue
        before = db.execute("select set_data from data_set where doc_name = ? and set_name = 'last_edit'",
                            (title,)).fetchone()
        rev = db.execute("select max(id + 0) from history where title = ?", (title,)).fetchone()[0] or 0
        for _, data, date, ip, send, leng, hide in new:
            rev += 1
            db.execute("insert into history (id, title, data, date, ip, send, leng, hide, type) "
                       "values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (str(rev), title, data, date, ip, (tag + (send or "")).strip(), leng or str(len(data or "")),
                        hide or "", "r1" if rev == 1 else ""))
            revs += 1
        if mine:
            db.execute("update data set data = ? where title = ?", (body, title))
        else:
            db.execute("insert into data (title, data, type) values (?, ?, '')", (title, body))
        sets = []
        if "data_set" in tables:
            sets = src.execute("select set_name, set_data from data_set where doc_name = ? and doc_rev = '' "
                               "and set_name in ('last_edit', 'length')", (title,)).fetchall()
        sets = dict(sets) or {}
        sets.setdefault("last_edit", new[-1][2])
        sets.setdefault("length", str(len(body)))
        db.execute("delete from data_set where doc_name = ? and set_name in ('last_edit', 'length')", (title,))
        db.executemany("insert into data_set (doc_name, doc_rev, set_name, set_data) values (?, '', ?, ?)",
                       [(title, k, v) for k, v in sets.items()])
        db.execute("delete from back where link = ?", (title,))
        if "back" in tables:
            db.executemany("insert into back (title, link, type, data) values (?, ?, ?, ?)",
                           src.execute("select title, link, type, data from back where link = ?", (title,)).fetchall())
        else:
            db.executemany("insert into back (link, title, type, data) values (?, ?, 'cat', '')",
                           [(title, "category:" + c.strip()) for c in set(CAT_RE.findall(body))])
        _, _, _, ip, send, _, _ = new[-1]
        m = NAMU_MOD_RE.search(send or "")
        if ip in NAMU_IPS and m:
            namu_title = "분류:" + title[9:] if title.startswith("category:") else title
            r = REDIRECT_RE.match(body)
            text = body if r else p2p.FOOTER_RE.sub("", body)
            uq.execute("insert or ignore into p2p_backup values (?, ?, ?, ?, ?)",
                       (namu_title, mine[0] if mine else None, before[0] if before else None, time.time(), node))
            p2p.record(uq, namu_title, text, r.group(1).strip() if r else "", m.group(1), "import", node=node, tier="import")
            checkable += 1
        added += 1
        if added % 2000 == 0:
            uq.commit()
            db.commit()
        prog.tick(i)
    db.commit()
    db.close()
    uq.commit()
    uq.close()
    src.close()
    print(f"가져온 문서 {added:,}개(판 {revs:,}개) · 건너뜀 {skipped:,}개(내 쪽이 같거나 더 새로움)", flush=True)
    if added:
        print(f"검증: 나무위키 판이라고 적힌 {checkable:,}개는 갱신기가 틈틈이 나무위키와 맞춰 봅니다(거짓이면 이 파일에서 가져온 것을 모두 되돌림)."
              + (f" 직접 편집한 판 {added - checkable:,}개는 나무위키와 비교할 수 없어 검증하지 않습니다." if added > checkable else ""),
              flush=True)
    return added


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["export", "import"])
    ap.add_argument("wiki_dir")
    ap.add_argument("file", nargs="?", default="")
    ap.add_argument("--range", choices=["all", "changed"], default="all")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    t0 = time.time()
    if args.action == "export":
        cut = cutoff(args.wiki_dir)
        out = args.out or os.path.join(os.path.dirname(os.path.abspath(args.wiki_dir)), "export",
                                       "yourwiki-opennamu-" + ("전체" if args.range == "all" else f"{cut}-이후") + ".db")
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        print(f"유어위키(openNAMU) 형식으로 내보내기 시작 → {out}", flush=True)
        print(NOTICE, flush=True)
        n = export(args.wiki_dir, out, args.range)
        print(f"완료: 문서 {n:,}개, {time.time() - t0:.0f}초 → {out}", flush=True)
    else:
        if not args.file:
            raise SystemExit("가져올 파일을 주세요")
        print(f"가져오기 시작 ← {args.file}", flush=True)
        n = import_pack(args.wiki_dir, args.file, args.range)
        print(f"완료: 문서 {n:,}개, {time.time() - t0:.0f}초", flush=True)


if __name__ == "__main__":
    sys.exit(main())
