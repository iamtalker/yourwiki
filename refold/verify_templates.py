"""틀 하나하나를 검증한다: 빈 openNAMU(3999번)에서 '틀 본문을 문서에 그대로 쓴 것'과 '[include(틀:이름)]' 이 같게 보이는지.
같지 않은 틀은 bad_templates.json 에 적고, refold_run.py 가 그 틀은 되접지 않는다.

사용: python verify_templates.py <scratch 폴더> [--limit N]
"""
import gzip
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.argv_backup = list(sys.argv)
import verify  # noqa: E402,F401  (updater 설정·page_text 재사용. SCR 은 첫 인자)

SCR = verify.SCR
STAMP = "2026-09-04 00:00:00"


def main():
    limit = 0
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    rows = []
    with gzip.open(os.path.join(HERE, "out", "templates_all.jsonl.gz"), "rt", encoding="utf-8") as f:
        for line in f:
            rows.append(json.loads(line))
    if limit:
        rows = rows[:limit]
    bad, details = [], []
    for k, t in enumerate(rows):
        name, text = t["title"], t["text"]
        verify.updater.apply(SCR, name, text, {}, STAMP)
        verify.updater.apply(SCR, f"검증/t/{k}/a", text, {}, STAMP)
        verify.updater.apply(SCR, f"검증/t/{k}/b", f"[include({name})]", {}, STAMP)
        a, _ = verify.page_text(f"검증/t/{k}/a")
        b, _ = verify.page_text(f"검증/t/{k}/b")
        if a is None or b is None or a != b:
            bad.append(name)
            j = next((x for x in range(min(len(a or ""), len(b or ""))) if a[x] != b[x]), 0) if a and b else 0
            details.append({"name": name, "a_len": len(a or ""), "b_len": len(b or ""),
                            "a": (a or "")[max(0, j - 30):j + 60], "b": (b or "")[max(0, j - 30):j + 60]})
        if (k + 1) % 300 == 0:
            print(f"{k + 1:,}/{len(rows):,} · 떨어진 틀 {len(bad):,}", flush=True)
    json.dump(bad, open(os.path.join(HERE, "bad_templates.json"), "w", encoding="utf-8"), ensure_ascii=False)
    json.dump(details, open(os.path.join(HERE, "bad_templates_detail.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"끝: 틀 {len(rows):,}개 중 렌더링이 달라지는 틀 {len(bad):,}개")


if __name__ == "__main__":
    main()
