"""7단계: 되접은 문서가 접기 전 문서와 같게 보이는지 빈 openNAMU(3999번)에서 렌더링해 비교한다.

준비: 빈 openNAMU 를 scratch 폴더에서 `main.amd64.exe 3999 --localhost` 로 켜 둔다(DB 는 scratch 의 data.db).
사용: python verify.py <scratch 폴더> [표본 수=120] [시드=1]
"""
import gzip
import html
import json
import os
import random
import re
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.join(HERE, "..", "yourwiki-kit", "scripts")
sys.path.insert(0, KIT)
import updater  # noqa: E402

updater.footer = lambda *a, **k: ""  # 출처 고지를 붙이지 않는다(실제 설치도 틀에는 붙이지 않음, 비교는 본문만)

SCR = sys.argv[1]
import time  # noqa: E402
STAMP = time.strftime("%Y-%m-%d %H:%M:%S")  # 실행마다 새 시각: 같은 날짜면 updater.apply 가 덮어쓰기를 건너뛰어 옛 문서와 비교하게 된다
N = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 120
random.seed(int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3].isdigit() else 1)
ORIG = os.path.join(HERE, "..", "work", "converted_2026.jsonl.gz")
NEW = os.path.join(HERE, os.environ.get("REFOLD_OUT", "out"), "converted_2026_refolded.jsonl.gz")
TPL = os.path.join(HERE, os.environ.get("REFOLD_OUT", "out"), "templates_refolded.jsonl.gz")
LIMIT = int(os.environ.get("VERIFY_LIMIT", "0"))  # 0 이면 전체에서 표본을 뽑는다(시험 실행은 앞쪽만 있으므로 그때 지정)


def page_text(title):
    url = "http://127.0.0.1:3999/w/" + urllib.parse.quote(title, safe="")
    try:
        h = urllib.request.urlopen(url, timeout=60).read().decode("utf-8")
    except Exception as e:  # noqa: BLE001
        return None, str(e)
    a = h.find('class="opennamu_main"')
    body = h[a:]
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", body, flags=re.S)
    cats = sorted(set(re.findall(r'href="/w/(?:category|%EB%B6%84%EB%A5%98)[^"]*"', body)))
    body = re.sub(r"<[^>]+>", "", body)
    return re.sub(r"\s+", "", html.unescape(body)), cats


def main():
    tpls = 0
    with gzip.open(TPL, "rt", encoding="utf-8") as f:
        for line in f:
            t = json.loads(line)
            updater.apply(SCR, t["title"], t["text"], {}, STAMP)
            tpls += 1
    print(f"틀 문서 {tpls:,}개 넣음", flush=True)
    sample, seen = [], 0  # 바뀐 문서 중 무작위 N 개(저수지 표본)
    with gzip.open(ORIG, "rt", encoding="utf-8") as fo, gzip.open(NEW, "rt", encoding="utf-8") as fn:
        for i, (lo, ln) in enumerate(zip(fo, fn)):
            if LIMIT and i >= LIMIT:
                break
            if lo == ln:
                continue
            o, n = json.loads(lo), json.loads(ln)
            if o["text"] == n["text"] or len(o["text"]) >= 60000:
                continue
            seen += 1
            item = (o["title"], o["text"], n["text"])
            if len(sample) < N:
                sample.append(item)
            else:
                k = random.randrange(seen)
                if k < N:
                    sample[k] = item
    print(f"바뀐 문서 {seen:,}개 중 {len(sample)}개를 뽑음", flush=True)
    same = diff = missing = 0
    bad = []
    for k, (title, ot, nt) in enumerate(sample):
        updater.apply(SCR, f"검증/o/{k}", ot, {}, STAMP)
        updater.apply(SCR, f"검증/r/{k}", nt, {}, STAMP)
        a, ca = page_text(f"검증/o/{k}")
        b, cb = page_text(f"검증/r/{k}")
        if a is None or b is None:
            missing += 1
            continue
        if a == b:
            same += 1
        else:
            diff += 1
            # 처음 달라지는 위치 근처를 보여 준다
            j = next((x for x in range(min(len(a), len(b))) if a[x] != b[x]), min(len(a), len(b)))
            bad.append((title, len(a), len(b), a[max(0, j - 40):j + 60], b[max(0, j - 40):j + 60]))
    print(f"비교 {len(sample)}개: 같음 {same} · 다름 {diff} · 못 읽음 {missing}")
    for x in bad[:8]:
        print("--", x[0], x[1], x[2])
        print("   원본:", x[3])
        print("   접음:", x[4])


if __name__ == "__main__":
    main()
