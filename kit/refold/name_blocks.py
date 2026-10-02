"""5단계: 반복 덩어리에 틀 이름을 붙인다.

기준(둘 다 만족해야 한다 — 틀과 덩어리가 서로를 가리켜야 안전하다):
 - 정밀도 P = (덩어리와 틀이 함께 나온 문서 수) / (덩어리가 나온 문서 중 2021 판 정보가 있는 문서 수) >= MIN_P
 - 재현율 R = (함께 나온 문서 수) / (2021 판에서 그 틀을 쓴 문서 수) >= MIN_R
 - 함께 나온 문서가 MIN_N 개 이상
한 틀에 덩어리가 여럿이면(매개변수 변형·판 차이) 점수(P*R)가 가장 높은 덩어리 하나만 그 틀이 된다.
결과: names.json — {"해시": {"name": 틀:이름, "df": …, "p": …, "r": …, "n": …}}
"""
import collections
import gzip
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MIN_P, MIN_R, MIN_N = 0.60, 0.10, 10


def main():
    name_df = collections.Counter()
    with gzip.open(os.path.join(HERE, "inc2021.jsonl.gz"), "rt", encoding="utf-8") as f:
        for line in f:
            for n in json.loads(line)["n"]:
                name_df[n] += 1
    best = {}  # 틀 이름 → (점수, 덩어리 정보)
    total = unnamed = 0
    cand = {}
    with gzip.open(os.path.join(HERE, "blocks.jsonl.gz"), "rt", encoding="utf-8") as f:
        for line in f:
            b = json.loads(line)
            total += 1
            if not b["both"] or not b["votes"]:
                continue
            for name, c in b["votes"]:
                p = c / b["both"]
                r = c / max(name_df[name], 1)
                if c >= MIN_N and p >= MIN_P and r >= MIN_R:
                    sc = p * r
                    if name not in best or sc > best[name][0]:
                        best[name] = (sc, {"h": b["h"], "name": name, "df": b["df"], "p": round(p, 3), "r": round(r, 3), "n": c})
                    break  # 이 덩어리는 가장 표가 많은 기준 통과 이름 하나만
    names = {v[1]["h"]: v[1] for v in best.values()}
    json.dump(names, open(os.path.join(HERE, "names.json"), "w", encoding="utf-8"), ensure_ascii=False)
    covered = sum(v["df"] for v in names.values())
    print(f"덩어리 {total:,}종 중 이름이 붙은 것 {len(names):,}종(틀 {len(best):,}개), 합친 사용처 {covered:,}곳")
    for v in sorted(names.values(), key=lambda x: -x["df"])[:40]:
        print(f'{v["df"]:>7,} P={v["p"]:.2f} R={v["r"]:.2f} {v["name"]}')


if __name__ == "__main__":
    main()
