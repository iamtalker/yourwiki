"""8단계-가: 한 줄짜리 매개변수 틀을 찾는다(상위 문서·상세 내용·분류 설명 …).

방법: 줄에서 [[…]] 를 구멍으로 바꾼 '뼈대'가 여러 문서에서 반복되면 후보.
메모리를 아끼려고 mine_a 와 같은 방식으로 한다:
 1) 뼈대의 해시를 64개 조각 파일에 쓰고, 조각마다 세어 MIN_DF 문서 이상인 뼈대만 남긴다(param_keep.bin).
 2) 한 번 더 읽으면서 남은 뼈대의 구멍 값 분포와 2021 판 틀 이름 투표를 모은다.
결과: param_cands.json — [{"skel", "df", "fixed": {구멍 번호: 고정값}, "params": [구멍 번호들], "both", "votes", "example"}]
사용: python param_a.py [최소 문서 수=300] [--units]
  --units: 줄이 아니라 덩어리(표·{{{#!wiki 상자) 전체의 뼈대를 센다 → param_cands_units.json (상자 안에 든 틀용)
"""
import collections
import gzip
import hashlib
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "work", "converted_2026.jsonl.gz")
UNITS = "--units" in sys.argv
PARTS = os.path.join(HERE, "pparts_units" if UNITS else "pparts")
CANDS = "param_cands_units.json" if UNITS else "param_cands.json"
LINK = re.compile(r"\[\[([^\[\]]+(?:\[\[[^\[\]]*\]\][^\[\]]*)*)\]\]")  # 안에 [[ ]] 가 한 층 들어 있어도 한 덩어리
SKIP_START = ("||", "{{{", "=", " *", "[[분류", "}}}")
NP = 64


def skeleton(line):
    vals = []

    def rep(m):
        vals.append(m.group(1))
        return "[[@]]"
    return LINK.sub(rep, line), vals


def lines_of(text):
    """뼈대 후보의 (뼈대, 값들, 원래 글). 줄 모드는 줄마다, --units 모드는 덩어리마다."""
    if UNITS:
        from units import units
        for u in units(text):
            if 60 <= len(u) <= 2000 and "[[" in u:
                sk, vs = skeleton(u)
                if sk != "[[@]]":
                    yield sk, vs, u
        return
    for ln in text.split("\n"):
        if not (20 <= len(ln) <= 400) or ln.startswith(SKIP_START) or "[[" not in ln:
            continue
        sk, vs = skeleton(ln)
        if sk != "[[@]]":
            yield sk, vs, ln


def h8(sk):
    return hashlib.md5(sk.encode("utf-8")).digest()[:8]


def main():
    min_df = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 300
    os.makedirs(PARTS, exist_ok=True)
    files = [open(os.path.join(PARTS, f"p{i:02d}.bin"), "wb") for i in range(NP)]
    bufs = [[] for _ in range(NP)]
    t0, docs = time.time(), 0
    with gzip.open(SRC, "rt", encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            if d.get("redirect") not in (None, "None", ""):
                continue
            docs += 1
            seen = set()
            for sk, _, _ in lines_of(d["text"]):
                h = h8(sk)
                if h in seen:
                    continue
                seen.add(h)
                bufs[h[0] % NP].append(h)
            if docs % 20000 == 0:
                for i in range(NP):
                    files[i].write(b"".join(bufs[i]))
                    bufs[i].clear()
            if docs % 200000 == 0:
                print(f"1차 {docs:,}개 · {time.time()-t0:.0f}초", flush=True)
    for i in range(NP):
        files[i].write(b"".join(bufs[i]))
        files[i].close()
    keep = set()
    for i in range(NP):
        p = os.path.join(PARTS, f"p{i:02d}.bin")
        b = open(p, "rb").read()
        c = collections.Counter(b[k:k + 8] for k in range(0, len(b), 8))
        keep.update(h for h, n in c.items() if n >= min_df)
        del b, c
        os.remove(p)
    print(f"{min_df}개 문서 이상 반복되는 뼈대 {len(keep):,}종 · 2차로 값 분포와 이름 투표를 모읍니다", flush=True)

    inc = {}
    with gzip.open(os.path.join(HERE, "inc2021.jsonl.gz"), "rt", encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            inc[d["t"]] = d["n"]
    df = collections.Counter()
    vals = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    votes = collections.defaultdict(collections.Counter)
    both = collections.Counter()
    skel_of, ex = {}, {}
    docs = 0
    with gzip.open(SRC, "rt", encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            if d.get("redirect") not in (None, "None", ""):
                continue
            docs += 1
            names = inc.get(d["title"])
            seen = set()
            for sk, vs, ln in lines_of(d["text"]):
                h = h8(sk)
                if h not in keep or h in seen:
                    continue
                seen.add(h)
                skel_of[h], ex[h] = sk, ln
                df[h] += 1
                if df[h] <= 3000:
                    for k, v in enumerate(vs):
                        vals[h][k][v] += 1
                if names:
                    both[h] += 1
                    for n in names:
                        votes[h][n] += 1
            if docs % 200000 == 0:
                print(f"2차 {docs:,}개 · {time.time()-t0:.0f}초", flush=True)
    out = []
    for h, c in df.most_common():
        fixed, params = {}, []
        for k in range(skel_of[h].count("[[@]]")):
            cn = vals[h][k]
            total = sum(cn.values())
            top, topn = cn.most_common(1)[0]
            if topn / total >= 0.9:  # 거의 안 변하는 구멍은 틀 본문에 고정
                fixed[k] = top
            else:
                params.append(k)
        out.append({"skel": skel_of[h], "df": c, "fixed": fixed, "params": params, "both": both[h],
                    "votes": votes[h].most_common(5), "example": ex[h]})
    json.dump(out, open(os.path.join(HERE, CANDS), "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    print(f"끝: 문서 {docs:,}개 · 뼈대 {len(out):,}종 · {time.time()-t0:.0f}초", flush=True)
    for o in out[:25]:
        print(o["df"], repr(o["skel"][:70]), "구멍", len(o["fixed"]) + len(o["params"]), "고정", len(o["fixed"]),
              "매개", len(o["params"]), o["votes"][:2])


if __name__ == "__main__":
    main()
