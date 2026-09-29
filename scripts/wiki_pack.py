"""유어위키(openNAMU) 형식으로 내보내기·가져오기 (유어위키 1.2, 표준 라이브러리만 사용).

내보낸 파일은 openNAMU 의 data.db 와 같은 SQLite 형식이다(문서 표 data·history·data_set·back 만).
사용자 계정·접속 기록·토론 같은 표는 넣지 않는다(비밀번호 해시·IP 가 있으므로).
'전체'로 내보낸 파일은 새 openNAMU 의 data.db 로 그대로 써도 된다(없는 표는 openNAMU 가 켤 때 만든다).

범위(내보내기만)
- 기간을 안 주면 모든 문서.
- 기간(--since, --until)을 주면 그 기간에 생긴 판만 담는다. 본문은 기간 안의 마지막 판.
  관리판의 기본 기간은 '기본 데이터 다음 날 ~ 오늘'이다(기본 데이터 = 설치한 덤프의 마지막 날짜).

가져오기는 범위 없이 파일에 든 것을 모두 본다. openNAMU 형식 파일(유어위키가 내보낸 것, 또는 다른 openNAMU 의 data.db)을 받는다.
- 문서마다 내 쪽 마지막 판보다 새 판만 역사 뒤에 이어 붙이고, 본문을 그 파일의 최신 본문으로 바꾼다.
- 내 쪽이 같거나 더 새로우면 건너뛴다(덮어쓰지 않는다). 지우기는 옮기지 않는다.
- 가져온 판은 역사의 '편집 요약' 앞에 [가져옴 파일이름] 을 붙여 어디서 왔는지 남긴다.
- 위키 엔진이 꺼져 있을 때만 한다(켜진 채 DB 를 크게 바꾸면 엔진이 꼬일 수 있다).

    python wiki_pack.py export WIKI_DIR [--since 2026-08-31] [--until 2026-09-29] [--out 파일]
    python wiki_pack.py import WIKI_DIR 파일
    python wiki_pack.py base WIKI_DIR      # 기본 데이터의 마지막 날짜와 기본 기간
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
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}$")


def base_end(wiki_dir):
    """기본 데이터(설치한 덤프)의 마지막 날짜. 역사에서 덤프가 남긴 판의 가장 늦은 날짜로 정하고,
    그게 없으면 wiki/edition.json 의 날짜, 그것도 없으면 ""."""
    try:
        db = ro(os.path.join(wiki_dir, "data.db"))
        try:
            d = max((db.execute("select max(date) from history where ip = ?", (ip,)).fetchone()[0] or "")
                    for ip in DUMP_IPS[:2])
        finally:
            db.close()
        if d:
            return d[:10]
    except sqlite3.Error:
        pass
    try:
        d = json.load(open(os.path.join(wiki_dir, "edition.json"), encoding="utf-8-sig")).get("date", "")
        if DATE_RE.match((d or "")[:10]):
            return d[:10]
    except (OSError, ValueError):
        pass
    return ""


def default_period(wiki_dir):
    """(기본 데이터 다음 날, 오늘). 기본 데이터를 모르면 (오늘 - 30일, 오늘)."""
    import datetime
    today = datetime.date.today()
    end = base_end(wiki_dir)
    start = (datetime.date.fromisoformat(end) + datetime.timedelta(days=1)) if end else today - datetime.timedelta(days=30)
    return start.isoformat(), today.isoformat()


def period_args(frm, to):
    """history.date 비교용 (시작 00:00:00, 끝 23:59:59)."""
    return (frm or "0000-00-00") + " 00:00:00", (to or "9999-12-31") + " 23:59:59"


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
def export(wiki_dir, out, frm="", to=""):
    """frm·to(YYYY-MM-DD) 가 없으면 전체, 있으면 그 기간에 생긴 판만."""
    src_path = os.path.join(wiki_dir, "data.db")
    period = bool(frm or to)
    lo_d, hi_d = period_args(frm, to)
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

    if period:
        # 기간 안의 판만. 본문은 기간 안의 마지막 판(그 뒤에 또 바뀌었어도 기간 끝 시점의 모습)
        dst.execute("create temp table pick (title text primary key, rev int)")
        dst.execute("insert into pick select title, max(id + 0) from s.history where date between ? and ? "
                    "group by title", (lo_d, hi_d))
        total = dst.execute("select count(*) from pick").fetchone()[0]
        print(f"{frm or '처음'} ~ {to or '오늘'} 에 바뀐 문서 {total:,}개를 내보냅니다", flush=True)
        dst.execute("insert into history select * from s.history where title in (select title from pick) "
                    "and date between ? and ?", (lo_d, hi_d))
        dst.execute("insert into data (title, data, type) select h.title, h.data, '' from history h "
                    "join pick p on p.title = h.title and h.id + 0 = p.rev")
        dst.execute("insert into data_set (doc_name, doc_rev, set_name, set_data) "
                    "select h.title, '', 'last_edit', h.date from history h join pick p on p.title = h.title "
                    "and h.id + 0 = p.rev")
        dst.execute("insert into data_set (doc_name, doc_rev, set_name, set_data) "
                    "select title, '', 'length', length(data) from data")
        dst.execute("insert into back select * from s.back where link in (select title from pick)")
        n = total
    else:
        total = dst.execute("select count(*) from s.data").fetchone()[0]
        print(f"모든 문서 {total:,}개를 내보냅니다", flush=True)
        prog, n = Progress(total), 0
        for t in TABLES:  # 진행을 보여 주며 나눠 옮긴다
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
        ("format", FORMAT), ("range", f"{frm}~{to}" if period else "all"),
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
                + (f"기간: {frm or '처음'} ~ {to or '오늘'} 에 생긴 판만.\n" if period
                   else "범위: 전체. 새 openNAMU 의 data.db 로 그대로 써도 됩니다.\n"))
    return n


# ---------------------------------------------------------------- 가져오기
def _has_index(db, table, first_col):
    for (name,) in db.execute("select name from sqlite_master where type = 'index' and tbl_name = ?", (table,)):
        cols = [r[2] for r in db.execute(f"pragma index_info('{name}')")]
        if cols and cols[0] == first_col:
            return True
    return False


def import_pack(wiki_dir, path):
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
    q = ("select title from data", ())
    total = src.execute("select count(*) from data").fetchone()[0]
    print(f"문서 {total:,}개를 살펴봅니다", flush=True)
    titles = (t for (t,) in ro(path).execute(*q))  # 제목 목록은 따로 연결해 흘려 읽는다(180만 개를 메모리에 올리지 않게)

    db = sqlite3.connect(os.path.join(wiki_dir, "data.db"), timeout=60)
    prog = Progress(total)
    added = skipped = revs = 0
    tag = f"[가져옴 {name}] "
    for i, title in enumerate(titles, 1):
        rows = src.execute("select id, data, date, ip, send, leng, hide from history where title = ? "
                           "order by id + 0", (title,)).fetchall()
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
        added += 1
        if added % 2000 == 0:
            db.commit()
        prog.tick(i)
    db.commit()
    db.close()
    src.close()
    print(f"가져온 문서 {added:,}개(판 {revs:,}개) · 건너뜀 {skipped:,}개(내 쪽이 같거나 더 새로움)", flush=True)
    return added


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["export", "import", "base"])
    ap.add_argument("wiki_dir")
    ap.add_argument("file", nargs="?", default="")
    ap.add_argument("--since", dest="frm", default="", help="내보낼 기간의 시작일 YYYY-MM-DD")
    ap.add_argument("--until", dest="to", default="", help="내보낼 기간의 끝일 YYYY-MM-DD")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    for d in (args.frm, args.to):
        if d and not DATE_RE.match(d):
            raise SystemExit(f"날짜는 YYYY-MM-DD 로: {d}")
    t0 = time.time()
    if args.action == "base":
        print("기본 데이터 마지막 날:", base_end(args.wiki_dir) or "모름", "· 기본 기간:", *default_period(args.wiki_dir))
    elif args.action == "export":
        tag = "전체" if not (args.frm or args.to) else f"{args.frm or '처음'}~{args.to or time.strftime('%Y-%m-%d')}"
        out = args.out or os.path.join(os.path.dirname(os.path.abspath(args.wiki_dir)), "export",
                                       f"yourwiki-opennamu-{tag}.db")
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        print(f"유어위키(openNAMU) 형식으로 내보내기 시작 → {out}", flush=True)
        print(NOTICE, flush=True)
        n = export(args.wiki_dir, out, args.frm, args.to)
        print(f"완료: 문서 {n:,}개, {time.time() - t0:.0f}초 → {out}", flush=True)
    else:
        if not args.file:
            raise SystemExit("가져올 파일을 주세요")
        print(f"가져오기 시작 ← {args.file}", flush=True)
        n = import_pack(args.wiki_dir, args.file)
        print(f"완료: 문서 {n:,}개, {time.time() - t0:.0f}초", flush=True)


if __name__ == "__main__":
    sys.exit(main())
