"""보충 틀 가져오기: extras/*.jsonl.gz → openNAMU data.db

2021 공식 덤프에 틀이 없어서 다른 출처의 틀로 채운다. 설치 스크립트는 파일 이름 순으로 넣으므로
우선순위가 높은 출처(나무위키 2020 공식 덤프)가 앞에 오도록 이름을 붙인다.
각 줄의 "source" 가 출처 표시가 되고, 없으면 알파위키 덤프로 본다.
- 이미 있는 제목은 건드리지 않는다.
- 틀 본문에 보이는 문구를 붙이면 그 틀을 쓰는 모든 문서에 문구가 끼어들므로,
  저작자 표시는 화면에 보이지 않는 주석(##)과 편집 역사(편집 요약)에 남긴다.

사용: python import_templates.py <틀.jsonl.gz> <openNAMU 폴더>
"""
import gzip
import json
import os
import re
import sqlite3
import sys

IFRAME_RE = re.compile(r"<iframe\b[^>]*?\bsrc=[\"']([^\"']+)[\"'][^>]*>\s*</iframe>", re.I | re.S)
GMAP_RE = re.compile(r"https://www\.google\.com/maps/embed/v1/\w+\?q=([^&]*)")


def unembed(text):
    """iframe 삽입을 '눌러야 열리는 링크'로 바꾼다.

    문서를 여는 것만으로 외부(구글 등)에 접속하지 않게 하고, 틀에 박힌 남의 API 키도 없앤다.
    """
    def repl(m):
        src = m.group(1)
        g = GMAP_RE.match(src)
        if g:
            place = g.group(1)
            return (f'<a href="https://www.google.com/maps/search/?api=1&query={place}"'
                    f' target="_blank" rel="noopener">🗺 구글 지도에서 {place} 보기</a>')
        src = re.sub(r"([?&])key=[^&]*&?", r"\1", src).rstrip("?&")
        return f'<a href="{src}" target="_blank" rel="noopener">▶ 외부 삽입 콘텐츠 열기</a>'
    return IFRAME_RE.sub(repl, text)


DEFAULT_SOURCE = "알파위키 2023-01-04 비공식 덤프 (원출처 나무위키, 더위키 배포)"
LICENSE = "CC BY-NC-SA 2.0 KR"


def main(src, wiki_dir):
    con = sqlite3.connect(os.path.join(wiki_dir, "data.db"))
    cur = con.cursor()
    added = skipped = 0
    with gzip.open(src, "rt", encoding="utf-8") as f:
        for line in f:
            t = json.loads(line)
            if cur.execute("select 1 from data where title = ?", (t["title"],)).fetchone():
                skipped += 1
                continue
            source = t.get("source", DEFAULT_SOURCE)
            from_namu = source.startswith("나무위키")
            names = ", ".join(e[2:] + "(나무위키)" if e.startswith("N:") else e + "(알파위키)"
                              for e in t["editors"])
            origin = f" {t['origin_url']}" if t.get("origin_url") else ""
            header = (f"## 출처: {source}{origin}\n"
                      f"## 라이선스: {LICENSE} · 저작권은 각 기여자에게 있습니다.\n"
                      f"## 기여자: {names}\n")
            data = header + unembed(t["text"])
            date = t["last_edit_date"] or "2023-01-04 00:00:00"
            cur.execute("insert into data (title, data, type) values (?, ?, '')", (t["title"], data))
            cur.execute(
                "insert into history (id, title, data, date, ip, send, leng, hide, type)"
                " values ('1', ?, ?, ?, ?, ?, ?, '', 'r1')",
                (t["title"], data, date, "나무위키 덤프" if from_namu else "알파위키 덤프",
                 f"{source}에서 가져옴 · {LICENSE}"[:512], str(len(data))))
            cur.executemany(
                "insert into data_set (doc_name, doc_rev, set_name, set_data) values (?, '', ?, ?)",
                [(t["title"], "last_edit", date), (t["title"], "length", str(len(data)))])
            added += 1
    total = cur.execute("select count(*) from data").fetchone()[0]
    cur.execute("delete from other where name = 'count_all_title'")
    cur.execute("insert into other (name, data, coverage) values ('count_all_title', ?, '')", (str(total),))
    con.commit()
    con.close()
    if added:  # 색인을 다시 만들게 한다
        version = os.path.join(wiki_dir, "data", "bleve.version")
        if os.path.exists(version):
            os.remove(version)
    print(f"보충 틀 {added:,}개 추가, {skipped:,}개는 이미 있어 건너뜀")


if __name__ == "__main__":
    main(*sys.argv[1:])
