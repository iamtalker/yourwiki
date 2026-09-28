"""(개발용) 나무위키 공식 덤프에서 틀만 뽑아 키트에 동봉할 파일을 만든다.

2021-03-01 공식 덤프는 틀(namespace 1)을 빼고 공개됐지만, 2020-03-02 덤프에는 들어 있다.
본문은 2021판을 쓰고, 틀은 이 파일로 채운다.

사용: python build_namu_templates.py <namuwiki200302.7z> <출력.jsonl.gz> [--7z 경로] [--date 2020-03-02]
"""
import argparse
import gzip
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from namudump import open_dump, read_docs  # noqa: E402

TEMPLATE_NS = (1, "1")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dump")
    ap.add_argument("out")
    ap.add_argument("--7z", dest="seven_zip", default="7z")
    ap.add_argument("--date", default="2020-03-02")
    args = ap.parse_args()

    source = f"나무위키 {args.date} 공식 덤프"
    stream, _ = open_dump(args.dump, args.seven_zip)
    n = 0
    with gzip.open(args.out, "wt", encoding="utf-8") as f:
        for doc in read_docs(stream):
            if doc["namespace"] not in TEMPLATE_NS:
                continue
            title = doc["title"] if doc["title"].startswith("틀:") else "틀:" + doc["title"]
            f.write(json.dumps({
                "title": title,
                "text": doc["text"],
                "editors": ["N:" + c for c in doc.get("contributors", []) if c],
                "last_edit_date": f"{args.date} 00:00:00",
                "source": source,
                "origin_url": "https://namu.wiki/w/" + title,
            }, ensure_ascii=False) + "\n")
            n += 1
    print(n, "개 틀 →", args.out)


if __name__ == "__main__":
    main()
