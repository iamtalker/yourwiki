"""실험 2: 변환본에서 '덩어리(표·{{{#!wiki}}})' 단위로 완전히 같은 것이 얼마나 반복되는지 센다."""
import collections, gzip, hashlib, json, sys, time

def units(text):
    """최상위 덩어리: 줄머리 `||` 로 이어지는 표, 줄머리 `{{{#!` 로 시작해 괄호가 맞을 때까지."""
    lines = text.split("\n"); i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("{{{#!"):
            depth = 0; j = i
            while j < len(lines):
                depth += lines[j].count("{{{") - lines[j].count("}}}")
                j += 1
                if depth <= 0: break
            if depth <= 0 and j - i <= 400:
                yield "\n".join(lines[i:j]); i = j; continue
        elif ln.startswith("||"):
            j = i
            while j < len(lines) and lines[j].startswith("||"): j += 1
            yield "\n".join(lines[i:j]); i = j; continue
        i += 1

N = int(sys.argv[1]); skip = int(sys.argv[2]) if len(sys.argv) > 2 else 0
cnt = collections.Counter(); size = {}; docs = chars = 0; t0 = time.time()
with gzip.open("../work/converted_2026.jsonl.gz", "rt", encoding="utf-8") as f:
    for i, line in enumerate(f):
        if i < skip: continue
        if i >= skip + N: break
        d = json.loads(line)
        if d.get("redirect") not in (None, "None", ""): continue
        t = d["text"]; docs += 1; chars += len(t)
        seen = set()
        for u in units(t):
            if len(u) < 80: continue
            h = hashlib.md5(u.encode()).digest()[:8]
            if h in seen: continue
            seen.add(h); cnt[h] += 1; size.setdefault(h, u)
for thr in (5, 20, 100):
    rep = [h for h, c in cnt.items() if c >= thr]
    cov = sum(cnt[h] * len(size[h]) for h in rep)
    print(f"{thr}회 이상 같은 덩어리 {len(rep):,}종 → 덮는 글자 {cov:,}/{chars:,} ({cov*100/chars:.1f}%)")
print(f"문서 {docs:,} · {time.time()-t0:.0f}초")
for h, c in cnt.most_common(8): print(c, repr(size[h][:160]))
