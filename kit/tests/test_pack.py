"""유어위키(openNAMU) 형식 내보내기·가져오기 시험: 설치한 판 이후 바뀐 것만 내보내 되돌린 위키에 넣으면 같아지는지."""
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACK = os.path.join(ROOT, "scripts", "wiki_pack.py")
COLS = "test text default '', "
SCHEMA = [f"create table data ({COLS}title text default '', data text default '', type text default '')",
          f"create table history ({COLS}id text default '', title text default '', data text default '', "
          "date text default '', ip text default '', send text default '', leng text default '', "
          "hide text default '', type text default '')",
          f"create table data_set ({COLS}doc_name text default '', doc_rev text default '', set_name text default '', "
          "set_data text default '')",
          f"create table back ({COLS}title text default '', link text default '', type text default '', "
          "data text default '')",
          "create table user_set (name text, id text, data text)"]


def make(path, changed=True):
    db = sqlite3.connect(path)
    for s in SCHEMA:
        db.execute(s)
    db.execute("insert into user_set values ('pw', 'admin', 'secret-hash')")  # 내보내면 안 되는 것
    for i in range(50):
        t = f"문서{i}"
        db.execute("insert into data (title, data) values (?, ?)", (t, f"덤프 본문 {i}"))
        db.execute("insert into history (id, title, data, date, ip, type) values ('1', ?, ?, '2026-08-20 00:00:00', "
                   "'나무위키 덤프', 'r1')", (t, f"덤프 본문 {i}"))
    if changed:
        for i in range(0, 50, 5):  # 10개는 설치 뒤 갱신됨
            t = f"문서{i}"
            db.execute("update data set data = ? where title = ?", (f"새 본문 {i} [[분류:시험]]", t))
            db.execute("insert into history (id, title, data, date, ip, send) values ('2', ?, ?, "
                       "'2026-09-10 00:00:00', '유어위키 갱신기', '나무위키 최신판(수정 2026-09-09 12:00:00)에서 갱신')", (t, f"새 본문 {i} [[분류:시험]]"))
            db.execute("insert into back (title, link, type) values ('category:시험', ?, 'cat')", (t,))
        db.execute("insert into data (title, data) values ('새 문서', '설치 뒤 생긴 문서')")
        db.execute("insert into history (id, title, data, date, ip, type) values ('1', '새 문서', '설치 뒤 생긴 문서', "
                   "'2026-09-11 00:00:00', '1.2.3.4', 'r1')")
    db.commit()
    db.close()


def run(*a):
    r = subprocess.run([sys.executable, PACK, *a], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


def main():
    d = tempfile.mkdtemp(prefix="yw-pack-")
    try:
        a, b = os.path.join(d, "A"), os.path.join(d, "B")
        os.makedirs(a)
        os.makedirs(b)
        make(os.path.join(a, "data.db"))
        make(os.path.join(b, "data.db"), changed=False)
        out = os.path.join(d, "changed.db")
        run("export", a, "--range", "changed", "--out", out)
        x = sqlite3.connect(out)
        assert x.execute("select count(*) from data").fetchone()[0] == 11
        assert x.execute("select count(*) from history where ip = '나무위키 덤프'").fetchone()[0] == 0
        assert "user_set" not in {r[0] for r in x.execute("select name from sqlite_master")}
        x.close()
        run("import", b, out, "--range", "changed")
        A = dict(sqlite3.connect(os.path.join(a, "data.db")).execute("select title, data from data"))
        B = dict(sqlite3.connect(os.path.join(b, "data.db")).execute("select title, data from data"))
        assert A == B, set(A.items()) ^ set(B.items())
        bdb = sqlite3.connect(os.path.join(b, "data.db"))
        assert bdb.execute("select id, type from history where title = '문서0' order by id + 0").fetchall() == \
            [("1", "r1"), ("2", "")]
        assert bdb.execute("select count(*) from back where link = '문서5' and title = 'category:시험'").fetchone()[0] == 1
        assert "가져온 문서 0개" in run("import", b, out)  # 다시 넣으면 모두 건너뜀
        # 검증: 나무위키 판이라고 적힌 10개(직접 편집 1개는 제외)가 검증 대기에 들어가야 한다
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import p2p
        q = sqlite3.connect(os.path.join(b, "updater.db"))
        rows = q.execute("select title, node, modified, audited from shared where src = 'import'").fetchall()
        assert len(rows) == 10 and all(r[1] == "file:changed.db" and r[2] == "2026-09-09 12:00:00" and r[3] == 0
                                       for r in rows), rows
        assert p2p.audit_candidate(q) in {r[0] for r in rows}
        # 하나는 나무위키와 같음 → 통과
        assert p2p.audit(q, b, "문서0", "새 본문 0 [[분류:시험]]", "", "2026-09-09 12:00:00") == "ok"
        # 하나는 같은 수정 시각인데 내용이 다름 → 그 파일에서 가져온 문서를 모두 되돌림
        assert p2p.audit(q, b, "문서5", "진짜 나무위키 본문", "", "2026-09-09 12:00:00") == "bad"
        bdb = sqlite3.connect(os.path.join(b, "data.db"))
        assert bdb.execute("select data from data where title = '문서10'").fetchone()[0] == "덤프 본문 10"
        assert bdb.execute("select data from data where title = '문서0'").fetchone()[0].startswith("새 본문 0")  # 검증 통과분은 유지
        assert bdb.execute("select data from data where title = '새 문서'").fetchone()[0] == "설치 뒤 생긴 문서"  # 검증 대상 아님
        assert q.execute("select count(*) from queue where reason = '사보타주 되돌림'").fetchone()[0] == 10  # 통과한 1개도 다시 받기 대기열에(내용은 유지)
        assert p2p.get_meta(q, "bad:file:changed.db")
        full = os.path.join(d, "all.db")
        run("export", a, "--out", full)
        assert sqlite3.connect(full).execute("select count(*) from data").fetchone()[0] == 51
        print("test_pack: 통과")
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    main()
