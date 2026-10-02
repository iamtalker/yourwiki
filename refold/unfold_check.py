"""되접은 글을 다시 펼쳐(틀 호출 → 틀 본문) 되접기 전 변환본과 같은지 비교한다.
사용: python unfold_check.py [문서 수=60000] [REFOLD_OUT=out2]
"""
import gzip
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from units import category_links  # noqa: E402

OUT = os.environ.get("REFOLD_OUT", "out2")
N = int(sys.argv[1]) if len(sys.argv) > 1 else 60000
INC = re.compile(r"\[include\((틀:[^,()\[\]]+?)(?:, ([^\n]*?))?\)\]")


def load_bodies():
    bodies = {}
    with gzip.open(os.path.join(HERE, OUT, "templates_refolded.jsonl.gz"), "rt", encoding="utf-8") as f:
        for line in f:
            t = json.loads(line)
            bodies[t["title"]] = t["text"]
    return bodies


def unfold(text, bodies):
    """틀 호출을 틀 본문으로 바꾸고, 호출 뒤에 붙여 두었던 분류 링크는 떼어 낸다."""
    def sub(m):
        body = bodies.get(m.group(1).strip())
        if body is None:
            return m.group(0)
        if m.group(2):  # 매개변수 틀: @이름@ 을 값으로 채운다
            for arg in m.group(2).split(", "):
                k, _, v = arg.partition("=")
                body = body.replace("@" + k.strip() + "@", v)
        return body + "\x00" + category_links(body)  # 뒤따르는 분류 링크 표시(아래에서 제거)

    out = INC.sub(sub, text)
    # 호출 뒤에 붙인 분류 링크 = 방금 계산한 category_links(body) 와 같은 글 → 표시(\x00) 뒤에 그대로 붙어 있으면 뗀다
    def strip(m):
        return ""
    res, i = [], 0
    while True:
        j = out.find("\x00", i)
        if j < 0:
            res.append(out[i:])
            break
        res.append(out[i:j])
        k = out.find("\x00", j + 1)  # 표시 뒤의 분류 글(길이는 위에서 정해짐)은 다음 단계에서 확인
        i = j + 1
        # 표시 뒤에 이어지는 글의 앞부분이 분류 링크 묶음이면 그만큼 뗀다
        m = re.match(r"(?:\[\[분류:[^\[\]\n]*\]\])+", out[i:])
        if m:
            i += m.end()
    return "".join(res)


def main():
    bodies = load_bodies()
    same = diff = changed = 0
    ex = []
    with gzip.open(os.path.join(HERE, "..", "work", "converted_2026.jsonl.gz"), "rt", encoding="utf-8") as fo, \
            gzip.open(os.path.join(HERE, OUT, "converted_2026_refolded.jsonl.gz"), "rt", encoding="utf-8") as fn:
        for i, (lo, ln) in enumerate(zip(fo, fn)):
            if i >= N:
                break
            o, n = json.loads(lo), json.loads(ln)
            if o["text"] == n["text"]:
                continue  # 되접히지 않은 문서
            changed += 1
            u = unfold(n["text"], bodies)
            if u == o["text"]:
                same += 1
            else:
                diff += 1
                if len(ex) < 3:
                    j = next((x for x in range(min(len(u), len(o["text"]))) if u[x] != o["text"][x]), min(len(u), len(o["text"])))
                    ex.append((o["title"], len(o["text"]), len(u), o["text"][max(0, j - 30):j + 50], u[max(0, j - 30):j + 50]))
    print(f"되접힌 문서 {changed:,}개 중 되펼친 결과가 원본과 글자까지 같은 것 {same:,}개({same*100/max(changed,1):.1f}%), 다른 것 {diff:,}개")
    for e in ex:
        print("--", e[0], e[1], e[2])
        print("   원본:", repr(e[3]))
        print("   펼침:", repr(e[4]))


if __name__ == "__main__":
    main()
