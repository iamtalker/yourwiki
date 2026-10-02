"""진단: 되접은 문서에서 어떤 틀 호출이 렌더링을 바꾸는지 찾는다.
사용: python diag.py <scratch 폴더> <제목> [REFOLD_OUT=out2]
되접은 글의 [include(...)] 를 하나씩 원래 덩어리(틀 본문)로 되돌려 렌더링하고, 원본 렌더링과 같아지는 경우를 찾는다.
"""
import gzip
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import verify  # noqa: E402,F401

OUT = os.environ.get("REFOLD_OUT", "out2")


def cats(text):
    return sorted(set(re.findall(r"\[\[분류:[^\[\]\n]*\]\]", text)))


def main():
    title = sys.argv[2]
    pairs = json.load(open(os.path.join(HERE, "pairs.json"), encoding="utf-8"))
    orig, new = pairs[title]["orig"], pairs[title]["new"]
    bodies = {}
    with gzip.open(os.path.join(HERE, OUT, "templates_refolded.jsonl.gz"), "rt", encoding="utf-8") as f:
        for line in f:
            t = json.loads(line)
            bodies[t["title"]] = t["text"]
    upd = verify.updater

    def render(doc, tag):
        upd.apply(verify.SCR, f"검증/d/{tag}", doc, {}, verify.STAMP)
        return verify.page_text(f"검증/d/{tag}")[0]
    ref = render(orig, "orig")
    cur = render(new, "new")
    print(f"원본 {len(ref)}자, 되접음 {len(cur)}자, 같음={ref == cur}")
    print("분류 원본:", cats(orig))
    print("분류 접음:", cats(new))
    incs = list(re.finditer(r"\[include\(([^,)]+)(?:,[^\n]*?)?\)\]", new))
    for k, m in enumerate(incs):
        name = m.group(1)
        body = bodies.get(name)
        if body is None:
            print(" ", name, "틀 본문 없음")
            continue
        variant = new[:m.start()] + (body if m.group(0).count(",") == 0 else m.group(0)) + new[m.end():]
        v = render(variant, f"v{k}")
        print(f"  {name:30} 이 호출만 펼치면: {'원본과 같음' if v == ref else '여전히 다름'}")


if __name__ == "__main__":
    main()
