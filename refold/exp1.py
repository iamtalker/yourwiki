"""실험 1: 매개변수 없는 틀만으로 표본 문서를 되접어 본다."""
import collections, gzip, json, sys, time
import tpl
N = int(sys.argv[1]) if len(sys.argv) > 1 else 60000
t0 = time.time()
tpls = tpl.load_templates()
fixed = tpl.fixed_templates(tpls)
idx = tpl.build_index(fixed)
print(f"틀 {len(tpls):,}개 중 매개변수 없이 펼쳐지는 틀 {len(fixed):,}개, 첫 줄 색인 {len(idx):,}개 ({time.time()-t0:.0f}초)")
docs = changed = reps = saved = total = 0
cnt = collections.Counter(); ex = []
with gzip.open("../work/converted_2026.jsonl.gz", "rt", encoding="utf-8") as f:
    for i, line in enumerate(f):
        if i >= N: break
        d = json.loads(line)
        if d.get("redirect") not in (None, "None", ""): continue
        t = d["text"]; docs += 1; total += len(t)
        new, used = tpl.refold_text(t, idx)
        if used:
            changed += 1; reps += len(used)
            saved += len(t) - len(new)
            for title, n in used: cnt[title] += 1
            if len(ex) < 3 and len(used) >= 2: ex.append((d["title"], used[:3]))
print(f"문서 {docs:,}개 · 되접은 문서 {changed:,}개({changed*100//docs}%) · 바꾼 곳 {reps:,} · 줄어든 글자 {saved:,}/{total:,} ({saved*100/total:.1f}%) · {time.time()-t0:.0f}초")
for title, c in cnt.most_common(15): print(c, title)
print(ex)
