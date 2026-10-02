"""2021 공식 덤프에서 문서마다 '불러 쓴 틀 이름'을 뽑는다(틀이 펼쳐진 2026 덩어리에 이름을 붙이는 단서).
결과: inc2021.jsonl.gz — 한 줄에 {"t": 제목, "n": [틀 이름들]}  (틀을 불러 쓴 문서만)
사용: python inc2021.py
"""
import gzip
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.join(HERE, "..", "yourwiki-kit")
sys.path.insert(0, os.path.join(KIT, "scripts"))
from namudump import open_dump, read_docs  # noqa: E402

INC = re.compile(r"\[include\((틀:[^,()\[\]]+?)\s*(?:,[^\[\]]*)?\)\]")


def main():
    dump = os.path.join(KIT, "data", "namuwiki210301.7z")
    stream, proc = open_dump(dump, os.path.join(KIT, "tools", "7zr.exe"))
    t0, n, withinc = time.time(), 0, 0
    with gzip.open(os.path.join(HERE, "inc2021.jsonl.gz"), "wt", encoding="utf-8") as out:
        for doc in read_docs(stream):
            n += 1
            text = doc.get("text") or ""
            if "[include(" not in text:
                continue
            names = sorted({m.group(1).strip() for m in INC.finditer(text)})
            if names:
                withinc += 1
                out.write(json.dumps({"t": doc["title"], "n": names}, ensure_ascii=False) + "\n")
            if n % 100000 == 0:
                print(f"{n:,}개 읽음 · 틀을 쓴 문서 {withinc:,}개 · {time.time()-t0:.0f}초", flush=True)
    if proc:
        proc.wait()
    print(f"끝: {n:,}개 중 틀을 쓴 문서 {withinc:,}개 · {time.time()-t0:.0f}초", flush=True)


if __name__ == "__main__":
    main()
