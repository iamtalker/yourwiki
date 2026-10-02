"""위키 엔진(openNAMU)이 켜질 때 걸리는 시간을 줄인다.

openNAMU 는 켤 때마다 모든 표의 모든 열에 대해 `select count(*) from 표 where 열 is null` 을 돌린다.
문서 표(data)는 본문이 커서 이 검사 한 번에 표 전체를 읽는다(열 3개 → 14GB 가량, 디스크가 식어 있으면 몇 분).
비어 있는 부분 색인(`where 열 is null`)을 만들어 두면 SQLite 가 표 대신 그 색인만 보고 검사를 끝낸다.
널 값이 생기면 색인에 들어가므로 검사 결과는 그대로다. 여러 번 해도 안전하고, 이미 있으면 바로 끝난다.

사용: python fast_start.py <data.db>   (위키 엔진이 꺼져 있을 때)
"""
import sqlite3
import sys

# 오픈나무가 켤 때 훑는 표 중 큰 것들(나머지는 작아서 상관없다)
TABLES = ("data", "history", "back", "data_set")


def ensure(db_path):
    """없는 부분 색인을 만든다. 새로 만든 개수를 돌려준다."""
    con = sqlite3.connect(db_path, timeout=60)
    made = 0
    try:
        have = {r[0] for r in con.execute("select name from sqlite_master where type='index'")}
        for t in TABLES:
            cols = [r[1] for r in con.execute(f'pragma table_info("{t}")')]
            for c in cols:
                name = f"nullchk_{t}_{c}"
                if name in have:
                    continue
                con.execute(f'create index if not exists "{name}" on "{t}"("{c}") where "{c}" is null')
                made += 1
        con.commit()
    finally:
        con.close()
    return made


if __name__ == "__main__":
    n = ensure(sys.argv[1])
    print(f"시작 속도 색인 {n}개 만듦" if n else "시작 속도 색인이 이미 있습니다")
