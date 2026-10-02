"""(생산용) 2026-08 크롤링 덤프(namu-html.sqlite) 전체를 나무마크로 되돌려 중간 파일로 쓴다.

- 문서마다 html2namu.convert 로 변환한다(CPU 여러 개로 병렬).
- 틀 문서도 변환한다. 틀 되접기(refold_templates.py)가 '틀을 펼치면 이런 모양'이라는 견본으로 쓴다.
  틀 문서 자체에 원본(2020 공식 덤프, 알파위키)을 쓸지는 내보내기 단계에서 고른다.
- 출력은 한 줄에 문서 하나({"id", "title", "text", "last_modified", "redirect"})인 .jsonl.gz.
  이어서 refold_templates.py(틀 되접기) → export_dump.py(공식 덤프 형식) 순으로 처리한다.

사용: python build_2026.py <namu-html.sqlite> <출력.jsonl.gz> [--workers N] [--limit N]
"""
import argparse
import gzip
import json
import multiprocessing as mp
import os
import pathlib
import sqlite3
import sys
import time
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import html2namu  # noqa: E402

_size_map = {}


def _init(size_map):
    global _size_map
    _size_map = size_map


def work(row):
    rid, title, blob, last_modified = row
    try:
        raw = zlib.decompress(blob).decode("utf-8", "replace")
        text, info = html2namu.convert(raw, _size_map, title)
        err = None
    except Exception as e:  # 한 문서가 실패해도 전체는 계속
        text, info, err = "", {}, f"{type(e).__name__}: {e}"
    return rid, title, text, info, last_modified, err


def rows(src, limit):
    con = sqlite3.connect(pathlib.Path(src).resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    q = "select id, title, html, last_modified from docs order by id"
    if limit:
        q += f" limit {int(limit)}"
    for r in con.execute(q):
        if r[2] is not None:
            yield r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out", help="중간 파일(.jsonl.gz): 한 줄에 문서 하나")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--classmap", default=os.path.join(os.path.dirname(__file__), "..", "assets", "classmap.json"))
    args = ap.parse_args()

    size_map = json.load(open(args.classmap, encoding="utf-8")).get("size", {})
    print(f"작업자 {args.workers}개", flush=True)
    n = errors = redirects = 0
    t0 = time.time()
    with open(args.out + ".errors.log", "w", encoding="utf-8") as err_log, \
            gzip.open(args.out, "wt", encoding="utf-8", compresslevel=3) as out, \
            mp.Pool(args.workers, initializer=_init, initargs=(size_map,)) as pool:
        for rid, title, text, info, lm, err in pool.imap_unordered(
                work, rows(args.src, args.limit), chunksize=32):
            if err:
                errors += 1
                err_log.write(f"{rid}\t{title}\t{err}\n")
                continue
            redirects += bool(info.get("redirect"))
            out.write(json.dumps({"id": rid, "title": title, "text": text,
                                  "last_modified": lm, "redirect": info.get("redirect")},
                                 ensure_ascii=False) + "\n")
            n += 1
            if n % 50000 == 0:
                print(f"  {n:,}개 ({(time.time() - t0) / 60:,.1f}분, 넘겨주기 {redirects:,}, 오류 {errors:,})",
                      flush=True)
    print(f"완료: {n:,}개 (넘겨주기 {redirects:,}, 오류 {errors:,}), {(time.time() - t0) / 60:,.1f}분", flush=True)


if __name__ == "__main__":
    main()
