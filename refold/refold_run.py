"""6단계: 변환본을 되접는다. 이름이 붙은 덩어리를 `[include(틀:이름)]` 한 줄로 바꾸고, 틀 문서를 만든다.

입력: ../work/converted_2026.jsonl.gz, names.json, blocks.jsonl.gz
출력: out/converted_2026_refolded.jsonl.gz (같은 형식, 글만 바뀜)
      out/templates_refolded.jsonl.gz     (틀 문서: title·text·editors·last_edit_date·source)
      out/report.json
사용: python refold_run.py [--limit N]   (N 은 시험용으로 앞쪽 문서만)
"""
import argparse
import gzip
import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tpl  # noqa: E402
from mine_a import MAX_LEN, MIN_LEN, SRC  # noqa: E402
from units import category_links, unit_spans  # noqa: E402
import params  # noqa: E402

OUT = os.path.join(HERE, os.environ.get("REFOLD_OUT", "out"))  # 환경 변수로 출력 폴더를 바꿀 수 있다(예: out2)
SOURCE = "namu.wiki 2026-08-29 크롤링 덤프에서 되접음(문서 본문에 펼쳐진 틀을 다시 틀로)"


def uhash(u):
    return hashlib.md5(u.encode("utf-8")).digest()[:8].hex()


INCLUDE_CAP = 5  # openNAMU 는 문서 하나에서 include 를 5개까지만 렌더링한다(빈 엔진 시험): 6번째부터는 내용이 사라진다


def refold_doc(text, names, pdefs=None):
    """덩어리와 한 줄짜리 매개변수 틀을 되접는다. 문서당 INCLUDE_CAP 개까지, 줄어드는 글자가 큰 것부터. (새 글, 틀 이름 목록)"""
    lines = text.split("\n")
    cands = []  # (줄 시작, 줄 끝+1, 바꿀 글, 줄어드는 글자 수, 틀 이름)
    for i, j in unit_spans(lines):
        u = "\n".join(lines[i:j])
        if not MIN_LEN <= len(u) <= MAX_LEN:
            continue
        v = names.get(uhash(u))
        if v:
            inc = f"[include({v['name']})]" + category_links(u)  # 분류는 문서에 남긴다
            cands.append((i, j, inc, len(u) - len(inc), v["name"]))
    if pdefs:
        cands.extend(params.param_candidates(lines, pdefs))
    budget = INCLUDE_CAP - text.count("[include(")  # 원래 문서에 있던 include 도 센다
    if not cands or budget <= 0:
        return text, []
    best = sorted(sorted(cands, key=lambda c: -c[3])[:budget], key=lambda c: c[0])
    out, last, used = [], 0, []
    for i, j, inc, _, name in best:
        if i < last:  # 겹치면 건너뜀
            continue
        out.extend(lines[last:i])
        out.append(inc)
        last = j
        used.append(name)
    out.extend(lines[last:])
    return "\n".join(out), used


def pick_template_bodies(names, block_text):
    """틀마다 본문을 정한다: 정리한 2020/알파위키 원본이 덩어리와 같으면 원본, 아니면 덩어리."""
    import glob
    originals = {}
    import glob
    for p in sorted(glob.glob(os.path.join(tpl.EXTRAS, "*.jsonl.gz"))):
        with gzip.open(p, "rt", encoding="utf-8") as f:
            for line in f:
                d = json.loads(line)
                originals.setdefault(d["title"], []).append(d)
    all_src = {k: [x["text"] for x in lst] for k, lst in originals.items()}  # 틀 이름 → 원문 판들
    bodies, used_orig = {}, 0
    for h, v in names.items():
        name, text = v["name"], block_text[h]
        body, editors, edate, source = text, [], None, SOURCE
        for d in originals.get(name, []):
            keep = all_src[name]
            all_src[name] = [d["text"]]  # 이 판 하나만으로 펼쳐 본다
            try:
                e = tpl.expand(name, all_src)
            finally:
                all_src[name] = keep
            if e is not None and tpl.norm(e) == tpl.norm(text):
                body, used_orig = tpl.clean_source(d["text"]), used_orig + 1
                editors = [x[2:] if x.startswith("N:") else x for x in d.get("editors", [])]
                edate, source = d.get("last_edit_date"), d.get("source") or "알파위키 2023-01-04 비공식 덤프"
                break
        if name in bodies:  # 같은 틀에 덩어리가 둘 이상으로 나뉜 경우는 name_blocks 가 하나만 남기므로 여기 오지 않는다
            continue
        bodies[name] = {"title": name, "text": body, "editors": editors, "last_edit_date": edate, "source": source}
    return bodies, used_orig


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--templates-only", action="store_true", help="문서는 건드리지 않고 이름 붙은 틀 전부를 out/templates_all.jsonl.gz 로 내보낸다(틀 검증용)")
    ap.add_argument("--exclude", default=os.path.join(HERE, "bad_templates.json"), help="되접기에서 뺄 틀 이름 목록(JSON, verify_templates.py 가 만듦)")
    ap.add_argument("--no-params", action="store_true", help="한 줄짜리 매개변수 틀(params.py)은 되접지 않는다")
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    pdefs = {} if a.no_params or a.templates_only else params.load_defs()
    if pdefs:
        print(f"매개변수 틀 {len(pdefs)}종 사용: " + ", ".join(sorted({d['name'] for d in pdefs.values()})), flush=True)
    names = json.load(open(os.path.join(HERE, "names.json"), encoding="utf-8"))
    block_text = {}
    with gzip.open(os.path.join(HERE, "blocks.jsonl.gz"), "rt", encoding="utf-8") as f:
        for line in f:
            b = json.loads(line)
            if b["h"] in names:
                block_text[b["h"]] = b["text"]
    if os.path.exists(a.exclude) and not a.templates_only:
        bad = set(json.load(open(a.exclude, encoding="utf-8")))
        names = {h: v for h, v in names.items() if v["name"] not in bad}
        print(f"검증에서 떨어진 틀 {len(bad):,}개는 되접지 않음", flush=True)
    print(f"이름 붙은 덩어리 {len(names):,}종 불러옴", flush=True)
    bodies, used_orig = pick_template_bodies(names, block_text)
    print(f"틀 문서 {len(bodies):,}개 (2020·알파위키 원본을 쓴 것 {used_orig:,}개)", flush=True)

    if a.templates_only:
        with gzip.open(os.path.join(OUT, "templates_all.jsonl.gz"), "wt", encoding="utf-8") as tf:
            for b in bodies.values():
                tf.write(json.dumps(b, ensure_ascii=False) + chr(10))
        print(f"틀 {len(bodies):,}개 → out/templates_all.jsonl.gz")
        return
    t0, docs, changed, reps, saved, chars = time.time(), 0, 0, 0, 0, 0
    per_tpl = {}
    with gzip.open(SRC, "rt", encoding="utf-8") as src, \
            gzip.open(os.path.join(OUT, "converted_2026_refolded.jsonl.gz"), "wt", encoding="utf-8") as out:
        for line in src:
            d = json.loads(line)
            docs += 1
            if a.limit and docs > a.limit:
                break
            if d.get("redirect") in (None, "None", ""):
                chars += len(d["text"])
                new, used = refold_doc(d["text"], names, pdefs)
                if used:
                    changed += 1
                    reps += len(used)
                    saved += len(d["text"]) - len(new)
                    for n in used:
                        per_tpl[n] = per_tpl.get(n, 0) + 1
                    d["text"] = new
            out.write(json.dumps(d, ensure_ascii=False) + "\n")
            if docs % 200000 == 0:
                print(f"{docs:,}개 · 바꾼 문서 {changed:,} · {time.time()-t0:.0f}초", flush=True)
    used_names = {n for n in per_tpl}
    with gzip.open(os.path.join(OUT, "templates_refolded.jsonl.gz"), "wt", encoding="utf-8") as tf:
        for n, b in bodies.items():
            if n in used_names:
                tf.write(json.dumps(b, ensure_ascii=False) + "\n")
        for d in pdefs.values():  # 매개변수 틀 문서(본문은 뼈대에서 만든 것)
            if d["name"] in used_names:
                tf.write(json.dumps({"title": d["name"], "text": d["body"], "editors": [], "last_edit_date": None,
                                     "source": SOURCE + " — 한 줄짜리 매개변수 틀"}, ensure_ascii=False) + "\n")
    rep = {"docs": docs, "changed_docs": changed, "replacements": reps, "chars_before": chars, "chars_saved": saved,
           "templates_used": len(used_names), "templates_from_original": used_orig,
           "top": sorted(per_tpl.items(), key=lambda x: -x[1])[:30]}
    json.dump(rep, open(os.path.join(OUT, "report.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"끝: 문서 {docs:,}개 중 {changed:,}개({changed*100//max(docs,1)}%)를 되접음 · 바꾼 곳 {reps:,} · "
          f"글자 {saved:,}/{chars:,} 감소({saved*100/max(chars,1):.1f}%) · 틀 {len(used_names):,}개 · {time.time()-t0:.0f}초", flush=True)


if __name__ == "__main__":
    main()
