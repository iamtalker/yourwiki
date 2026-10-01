"""'전체 문서 수'(other.count_all_title) 시험: 갱신기·가져오기가 새 문서를 넣으면 숫자가 늘고, 어긋난 값은 다시 세어 바로잡는지."""
import os
import shutil
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import updater  # noqa: E402

COLS = "test text default '', "
SCHEMA = [f"create table data ({COLS}title text default '', data text default '', type text default '')",
          f"create table history ({COLS}id text default '', title text default '', data text default '', "
          "date text default '', ip text default '', send text default '', leng text default '', hide text default '', "
          "type text default '')",
          f"create table data_set ({COLS}doc_name text default '', doc_rev text default '', set_name text default '', "
          "set_data text default '')",
          f"create table back ({COLS}title text default '', link text default '', type text default '', data text default '')",
          f"create table other ({COLS}name text default '', data text default '', coverage text default '')"]
fails = 0


def check(cond, msg):
    global fails
    if not cond:
        fails += 1
        print("실패:", msg)


def count_row(d):
    db = sqlite3.connect(os.path.join(d, "data.db"))
    r = db.execute("select data from other where name = 'count_all_title'").fetchone()
    n = db.execute("select count(*) from data").fetchone()[0]
    db.close()
    return (int(r[0]) if r else None), n


d = tempfile.mkdtemp(prefix="yw-count-")
try:
    db = sqlite3.connect(os.path.join(d, "data.db"))
    for s in SCHEMA:
        db.execute(s)
    db.execute("insert into data (title, data) values ('기존', '내용')")
    db.execute("insert into other (name, data, coverage) values ('count_all_title', '1', '')")
    db.commit()
    db.close()
    check(updater.apply(d, "새 문서", "본문", {}, "2026-10-01 10:00:00"), "새 문서 넣기")
    check(count_row(d) == (2, 2), f"새 문서를 넣으면 숫자가 늘어야 함: {count_row(d)}")
    check(updater.apply(d, "새 문서", "고친 본문", {}, "2026-10-02 10:00:00"), "같은 문서 갱신")
    check(count_row(d) == (2, 2), f"기존 문서를 갱신하면 숫자가 그대로여야 함: {count_row(d)}")
    db = sqlite3.connect(os.path.join(d, "data.db"))  # 옛 판처럼 어긋난 값
    db.execute("update other set data = '1' where name = 'count_all_title'")
    db.commit()
    db.close()
    old, n = updater.sync_count(d)
    check((old, n) == (1, 2), f"옛 값과 센 값: {(old, n)}")
    check(count_row(d) == (2, 2), f"다시 세면 맞아야 함: {count_row(d)}")
finally:
    shutil.rmtree(d, ignore_errors=True)

print("문서 수 시험 통과" if not fails else f"문서 수 시험 {fails}개 실패")
sys.exit(1 if fails else 0)
