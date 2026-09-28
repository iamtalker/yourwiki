"""(개발용) 알파위키 비공식 덤프에서 틀만 뽑아 키트에 동봉할 파일을 만든다.

알파위키 덤프는 Proton Drive 에만 있어 설치 스크립트가 자동으로 받을 수 없으므로,
CC BY-NC-SA 에 따라 틀(namespace 1)만 추려 extras/ 에 넣는다.

사용: python build_alpha_templates.py <alphawiki_unofficialdump_230104.7z> <출력.jsonl.gz> [--7z 경로]
"""
import argparse
import gzip
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from namudump import open_dump, read_docs  # noqa: E402

TEMPLATE_NS = 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dump")
    ap.add_argument("out")
    ap.add_argument("--7z", dest="seven_zip", default="7z")
    args = ap.parse_args()

    stream, _ = open_dump(args.dump, args.seven_zip)
    n = 0
    with gzip.open(args.out, "wt", encoding="utf-8") as f:
        for doc in read_docs(stream):
            if doc["namespace"] != TEMPLATE_NS:
                continue
            editors = []
            for h in doc.get("history", []):
                e = h.get("editor", "")
                if e and e != "External Importer" and e not in editors:
                    editors.append(e)
            f.write(json.dumps({
                "title": "틀:" + doc["title"],
                "text": doc["raw"],
                "editors": editors,
                "last_edit_date": doc.get("last_edit_date", ""),
            }, ensure_ascii=False) + "\n")
            n += 1
    print(n, "개 틀 →", args.out)


if __name__ == "__main__":
    main()
