"""(생산용) 변환 중간 파일(.jsonl.gz) → 나무위키 공식 덤프와 같은 형식의 JSON 배열.

공식 덤프 형식([{"namespace", "title", "text", "contributors"}, ...])을 따르므로
이 키트의 import_dump.py 뿐 아니라 공식 덤프를 읽던 다른 도구에서도 그대로 쓸 수 있다.
추가 필드: last_modified(원 문서 최근 수정 시각), source(출처 설명).
틀 문서는 매개변수가 살아 있는 원본(extras/*.jsonl.gz, 파일 이름 순서가 우선순위)을 쓴다.

사용: python export_dump.py <converted.jsonl.gz> <출력.json> [--templates extras 폴더]
"""
import argparse
import glob
import gzip
import json
import os

SOURCE = "namu.wiki 2026-08-29 크롤링 덤프(archive.org namuwiki-data-dump-20260829)를 나무마크로 되돌림"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--templates", default=os.path.join(os.path.dirname(__file__), "..", "extras"))
    args = ap.parse_args()

    originals = {}
    for p in sorted(glob.glob(os.path.join(args.templates, "*.jsonl.gz"))):
        with gzip.open(p, "rt", encoding="utf-8") as f:
            for line in f:
                t = json.loads(line)
                originals.setdefault(t["title"], t)

    n = replaced = 0
    seen = set()
    with gzip.open(args.src, "rt", encoding="utf-8") as src, open(args.out, "w", encoding="utf-8") as out:
        out.write("[")
        for line in src:
            d = json.loads(line)
            if d["title"] in seen:
                continue
            seen.add(d["title"])
            rec = {"namespace": 0, "title": d["title"], "text": d["text"], "contributors": [],
                   "last_modified": d.get("last_modified"), "source": SOURCE}
            o = originals.get(d["title"])
            if o:
                rec["text"], rec["contributors"] = o["text"], [e[2:] if e.startswith("N:") else e for e in o["editors"]]
                rec["source"] = o.get("source", "알파위키 2023-01-04 비공식 덤프")
                replaced += 1
            out.write(("," if n else "") + json.dumps(rec, ensure_ascii=False))
            n += 1
        # 2026 덤프에 없는 틀도 원본으로 넣는다
        for title, o in originals.items():
            if title in seen:
                continue
            rec = {"namespace": 0, "title": title, "text": o["text"],
                   "contributors": [e[2:] if e.startswith("N:") else e for e in o["editors"]],
                   "last_modified": o.get("last_modified"), "source": o.get("source", "알파위키 2023-01-04 비공식 덤프")}
            out.write("," + json.dumps(rec, ensure_ascii=False))
            n += 1
        out.write("]")
    print(f"{n:,}개 문서 (틀 원본으로 바꾼 것 {replaced:,}개) → {args.out}")


if __name__ == "__main__":
    main()
