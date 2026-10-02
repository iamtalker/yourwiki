"""실험 3: 한 줄짜리 틀(매개변수 있는 틀)의 '뼈대' — 줄에서 [[링크]] 안의 글만 바꿔 놓고 반복되는 줄을 센다."""
import collections, gzip, json, re, sys
N = int(sys.argv[1]) if len(sys.argv) > 1 else 60000
LINK = re.compile(r"\[\[([^\[\]|]+)(?:\|[^\[\]]*)?\]\]")
cnt = collections.Counter(); ex = {}; docs = 0
with gzip.open("../work/converted_2026.jsonl.gz", "rt", encoding="utf-8") as f:
    for i, line in enumerate(f):
        if i >= N: break
        d = json.loads(line)
        if d.get("redirect") not in (None, "None", ""): continue
        docs += 1
        seen = set()
        for ln in d["text"].split("\n"):
            if not (20 <= len(ln) <= 400) or ln.startswith(("||", "{{{", "=", " *", "[[분류")): continue
            if "[[" not in ln: continue
            sk = LINK.sub("[[@]]", ln)
            if sk in seen: continue
            seen.add(sk); cnt[sk] += 1; ex.setdefault(sk, ln)
for sk, c in cnt.most_common(14):
    print(c, repr(sk[:120]), "|예:", repr(ex[sk][:80]))
