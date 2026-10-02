"""2단계(전체): keep.bin 의 덩어리마다 본문을 모으고, 그 덩어리가 나온 문서들이 2021 판에서 불러 쓴 틀 이름을 투표로 센다.
결과: blocks.jsonl.gz — {"h": 해시, "df": 나온 문서 수, "both": 그중 2021 판에 틀 정보가 있는 문서 수, "text": 덩어리, "votes": [[틀 이름, 표 수], …]}
"""
import collections
import gzip
import json
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from mine_a import MAX_LEN, MIN_LEN, SRC, uhash, units  # noqa: E402


def main():
    keep = {}
    b = open(os.path.join(HERE, "keep.bin"), "rb").read()
    for k in range(0, len(b), 12):
        keep[b[k:k + 8]] = struct.unpack("<I", b[k + 8:k + 12])[0]
    inc = {}
    with gzip.open(os.path.join(HERE, "inc2021.jsonl.gz"), "rt", encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            inc[d["t"]] = d["n"]
    print(f"덩어리 {len(keep):,}종, 2021 틀 정보 {len(inc):,}개 문서", flush=True)
    text, both, votes = {}, collections.Counter(), collections.defaultdict(collections.Counter)
    t0, docs = time.time(), 0
    with gzip.open(SRC, "rt", encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            if d.get("redirect") not in (None, "None", ""):
                continue
            docs += 1
            names = inc.get(d["title"])
            seen = set()
            for u in units(d["text"]):
                if not MIN_LEN <= len(u) <= MAX_LEN:
                    continue
                h = uhash(u)
                if h in seen or h not in keep:
                    continue
                seen.add(h)
                text.setdefault(h, u)
                if names:
                    both[h] += 1
                    for n in names:
                        votes[h][n] += 1
            if docs % 100000 == 0:
                print(f"{docs:,}개 · {time.time()-t0:.0f}초", flush=True)
    with gzip.open(os.path.join(HERE, "blocks.jsonl.gz"), "wt", encoding="utf-8") as out:
        for h, df in sorted(keep.items(), key=lambda x: -x[1]):
            out.write(json.dumps({"h": h.hex(), "df": df, "both": both[h], "text": text.get(h, ""),
                                  "votes": votes[h].most_common(6)}, ensure_ascii=False) + "\n")
    print(f"끝: {len(keep):,}종 · {time.time()-t0:.0f}초", flush=True)


if __name__ == "__main__":
    main()
