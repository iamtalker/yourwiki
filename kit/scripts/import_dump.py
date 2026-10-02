"""나무위키 공식 JSON 덤프 → openNAMU SQLite(data.db) 전체 가져오기.

- .7z 를 풀지 않고 스트리밍으로 읽는다(디스크에 8.8GB 중간 파일을 만들지 않음).
- 모든 일반 문서 끝에 원 나무위키 URL, 기여자, CC BY-NC-SA 2.0 KR 고지를 삽입한다.
  넘겨주기(#redirect) 문서는 넘겨주기가 깨지지 않도록 본문을 그대로 둔다.
- history 는 1판만 만든다. 편집 요약에 출처와 원문 SHA-256 을 남긴다.
  --history light(기본)는 판 본문을 비워 용량을 절반 가까이 줄인다.
- 분류 문서는 openNAMU 규칙대로 category:X 로 넣고, 분류·넘겨주기 연결을 back 표에 넣는다.
  틀 문서에는 보이는 고지를 붙이지 않는다(그 틀을 쓰는 모든 문서에 끼어들기 때문).
- 끝나면 검색 색인 버전 파일을 지워 openNAMU 가 다음 시작 때 색인을 다시 만들게 한다.

openNAMU 를 한 번 실행해 data.db 를 만든 뒤, 서버를 멈춘 상태에서 실행한다.

사용: python import_dump.py <덤프.7z|.json> <openNAMU 폴더> [--7z 경로] [--history light|full]
      [--dump-date YYYY-MM-DD]
      [--skip-ns 4,8] [--limit N]
"""
import argparse
import hashlib
import os
import re
import sqlite3
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # 임베디드 파이썬은 스크립트 폴더를 path에 넣지 않음
from namudump import open_dump, read_docs  # noqa: E402

DUMP_DATE = "2026-08-29"  # --dump-date 로 바뀜(설치 스크립트가 항상 넘김)
LICENSE_URL ="https://creativecommons.org/licenses/by-nc-sa/2.0/kr/"
BATCH = 5000

# 덤프의 숫자 namespace → 문서 제목 접두어 (0 = 일반 문서)
NAMESPACES = {}


def namu_url(title):
    return "https://namu.wiki/w/" + urllib.parse.quote(title, safe="")


def is_redirect(text):
    return text.lstrip().lower().startswith("#redirect")


def attribution(title, contributors, doc=None):
    names = [c for c in contributors if c]
    if doc and doc.get("last_modified") and not names:
        # 크롤링 덤프: 기여자 목록이 없으므로 원 문서의 역사로 안내한다
        q = urllib.parse.quote(title, safe="")
        return (
            "\n\n----\n"
            f" * 출처: [[{namu_url(title)}|나무위키 「{title}」 문서]] (최근 수정 {doc['last_modified']}, {DUMP_DATE} 덤프)\n"
            f" * 라이선스: [[{LICENSE_URL}|CC BY-NC-SA 2.0 KR]] · 저작권은 각 기여자에게 있습니다. "
            f"기여자 목록은 [[https://namu.wiki/history/{q}|원 문서의 역사]]에서 볼 수 있습니다.\n"
        )
    return (
        "\n\n----\n"
        f" * 출처: [[{namu_url(title)}|나무위키 「{title}」 문서]] ({DUMP_DATE} 공식 덤프)\n"
        f" * 라이선스: [[{LICENSE_URL}|CC BY-NC-SA 2.0 KR]]"
        " · 저작권은 각 기여자에게 있습니다.\n"
        f" * 기여자 {len(names)}명: "
        "{{{#!folding [펼치기·접기]\n"
        + ", ".join(names).replace("}}}", "} } }")
        + "\n}}}\n"
    )


def fmt_secs(sec):
    sec = int(sec)
    if sec < 60:
        return f"{sec}초"
    if sec < 3600:
        return f"{sec // 60}분"
    return f"{sec // 3600}시간 {sec % 3600 // 60}분"


def full_title(doc):
    ns = doc["namespace"]
    title = doc["title"]
    prefix = NAMESPACES.get(ns, "" if ns in (0, "0", "") else str(ns))
    if prefix and not title.startswith(prefix + ":"):
        title = f"{prefix}:{title}"
    return title


def wiki_title(title):
    """openNAMU 는 분류 문서를 'category:X' 제목으로 다룬다."""
    return "category:" + title[3:] if title.startswith("분류:") else title


CAT_RE = re.compile(r"\[\[분류:([^\]|#\n]+)")
REDIRECT_RE = re.compile(r"^\s*#redirect\s+([^\n#]+)", re.I)


def back_links(text):
    """분류·넘겨주기 연결(back 표). 일반 링크는 양이 너무 많아 넣지 않는다."""
    m = REDIRECT_RE.match(text)
    if m:
        return {(wiki_title(m.group(1).strip()), "redirect")}
    return {("category:" + c.strip(), "cat") for c in CAT_RE.findall(text)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dump")
    ap.add_argument("wiki_dir")
    ap.add_argument("--7z", dest="seven_zip", default="7z")
    ap.add_argument("--history", choices=["light", "full"], default="light")
    ap.add_argument("--skip-ns", default="", help="제외할 namespace 번호, 쉼표 구분")
    ap.add_argument("--dump-date", default="2026-08-29", help="덤프 기준일(저작자 표시에 사용)")
    ap.add_argument("--limit", type=int, default=0, help="시험용: N개만 가져오기")
    ap.add_argument("--expected", type=int, default=0, help="예상 문서 수(진행률·남은 시간 표시용)")
    args = ap.parse_args()
    global DUMP_DATE
    DUMP_DATE = args.dump_date

    skip = {s.strip() for s in args.skip_ns.split(",") if s.strip()}
    db_path = os.path.join(args.wiki_dir, "data.db")
    if not os.path.exists(db_path):
        sys.exit(f"{db_path} 가 없습니다. openNAMU 를 한 번 실행해 DB 를 만든 뒤 다시 실행하세요.")

    con = sqlite3.connect(db_path)
    con.execute("pragma synchronous = off")
    con.execute("pragma journal_mode = memory")
    cur = con.cursor()
    seen = {row[0] for row in cur.execute("select title from data")}

    stream, proc = open_dump(args.dump, args.seven_zip)
    data_rows, hist_rows, set_rows, back_rows = [], [], [], []
    n = skipped = 0
    t0 = time.time()

    def flush():
        cur.executemany("insert into data (title, data, type) values (?, ?, '')", data_rows)
        cur.executemany(
            "insert into history (id, title, data, date, ip, send, leng, hide, type)"
            " values ('1', ?, ?, ?, '나무위키 덤프', ?, ?, '', 'r1')", hist_rows)
        cur.executemany(
            "insert into data_set (doc_name, doc_rev, set_name, set_data) values (?, '', ?, ?)",
            set_rows)
        cur.executemany("insert into back (link, title, type, data) values (?, ?, ?, '')", back_rows)
        con.commit()
        data_rows.clear(), hist_rows.clear(), set_rows.clear(), back_rows.clear()

    date = f"{DUMP_DATE} 00:00:00"
    for doc in read_docs(stream):
        if str(doc["namespace"]) in skip:
            skipped += 1
            continue
        orig_title = full_title(doc)
        title = wiki_title(orig_title)
        if title in seen or "\n" in title or "\r" in title:
            skipped += 1
            continue
        seen.add(title)
        text = doc["text"]
        if is_redirect(text) or orig_title.startswith("틀:"):
            data = text
        else:
            data = text.rstrip("\n") + attribution(orig_title, doc.get("contributors", []), doc)
        back_rows.extend((title, t, k) for t, k in back_links(text))
        send = (f"나무위키 {DUMP_DATE} 덤프에서 가져옴 "
                f"sha256={hashlib.sha256(text.encode()).hexdigest()[:16]}")
        length = str(len(data))
        data_rows.append((title, data))
        hist_rows.append((title, data if args.history == "full" else "", date, send, length))
        set_rows.append((title, "last_edit", date))
        set_rows.append((title, "length", length))
        n += 1
        if len(data_rows) >= BATCH:
            flush()
        if n % 50000 == 0:
            el = time.time() - t0
            if args.expected:
                left = max(args.expected - n, 0) * el / n
                print(f"  진행 {n:,} / 약 {args.expected:,}개 ({min(99, n * 100 // args.expected)}%) · "
                      f"{fmt_secs(el)} 지남 · 남은 시간 약 {fmt_secs(left)}", flush=True)
            else:
                print(f"  진행 {n:,}개 · {fmt_secs(el)} 지남", flush=True)
        if args.limit and n >= args.limit:
            break
    flush()
    if proc:
        proc.kill()

    total = cur.execute("select count(*) from data").fetchone()[0]
    cur.execute("delete from other where name = 'count_all_title'")
    cur.execute("insert into other (name, data, coverage) values ('count_all_title', ?, '')",
                (str(total),))
    con.commit()
    con.close()

    version = os.path.join(args.wiki_dir, "data", "bleve.version")
    if os.path.exists(version):
        os.remove(version)

    print(f"완료: {n:,}개 가져옴, {skipped:,}개 건너뜀, 총 {total:,}개, "
          f"{time.time() - t0:,.0f}초")


if __name__ == "__main__":
    main()
