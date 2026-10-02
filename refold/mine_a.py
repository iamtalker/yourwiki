"""1단계(전체): 변환본 179만 문서에서 '덩어리'(표·{{{#!wiki}}})의 해시를 세어, 20개 문서 이상에 나온 것만 keep.bin 에 남긴다.

메모리를 아끼려고 해시를 64개 조각 파일에 나눠 쓴 뒤 조각마다 센다.
사용: python mine_a.py [최소 문서 수=20]
"""
import collections
import gzip
import hashlib
import json
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
SRC = os.path.join(HERE, "..", "work", "converted_2026.jsonl.gz")
PARTS = os.path.join(HERE, "parts")
NP = 64
MIN_LEN, MAX_LEN = 80, 20000


from units import units  # noqa: E402,F401  (되접기 전 단계가 같은 덩어리 규칙을 쓴다)


def uhash(u):
    return hashlib.md5(u.encode("utf-8")).digest()[:8]


def main():
    MIN_DF = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    os.makedirs(PARTS, exist_ok=True)
    files = [open(os.path.join(PARTS, f"p{i:02d}.bin"), "wb") for i in range(NP)]
    bufs = [[] for _ in range(NP)]
    t0, docs, nunits = time.time(), 0, 0
    with gzip.open(SRC, "rt", encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            if d.get("redirect") not in (None, "None", ""):
                continue
            docs += 1
            seen = set()
            for u in units(d["text"]):
                if not MIN_LEN <= len(u) <= MAX_LEN:
                    continue
                h = uhash(u)
                if h in seen:
                    continue
                seen.add(h)
                bufs[h[0] % NP].append(h)
                nunits += 1
            if docs % 20000 == 0:
                for i in range(NP):
                    files[i].write(b"".join(bufs[i]))
                    bufs[i].clear()
                print(f"읽는 중 {docs:,}개 · 덩어리 {nunits:,}개 · {time.time()-t0:.0f}초", flush=True)
    for i in range(NP):
        files[i].write(b"".join(bufs[i]))
        files[i].close()
    print(f"읽기 끝: 문서 {docs:,}개 · 덩어리 {nunits:,}개 · {time.time()-t0:.0f}초. 조각별로 셉니다", flush=True)
    keep, total_keep = open(os.path.join(HERE, "keep.bin"), "wb"), 0
    for i in range(NP):
        p = os.path.join(PARTS, f"p{i:02d}.bin")
        b = open(p, "rb").read()
        c = collections.Counter(b[k:k + 8] for k in range(0, len(b), 8))
        for h, n in c.items():
            if n >= MIN_DF:
                keep.write(h + struct.pack("<I", n))
                total_keep += 1
        os.remove(p)
    keep.close()
    json.dump({"docs": docs, "units": nunits, "min_df": MIN_DF, "keep": total_keep},
              open(os.path.join(HERE, "mine_a.json"), "w"))
    print(f"끝: {MIN_DF}개 문서 이상에 나온 덩어리 {total_keep:,}종 · {time.time()-t0:.0f}초", flush=True)


if __name__ == "__main__":
    main()
