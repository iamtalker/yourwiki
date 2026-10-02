"""틀 되접기 공통 도구: 틀 원문 읽기·정리, 매개변수 없는 틀의 펼친 모양 만들기, 문서 안에서 같은 덩어리 찾기.

원리: 문서(변환본)에는 틀이 이미 펼쳐져 있다. 틀 원문(2020 덤프·알파위키)에서 '펼친 모양'을 만들어,
문서의 줄들을 공백 없이 이어 붙인 것과 같으면 그 줄들을 `[include(틀:이름)]` 한 줄로 바꾼다.
"""
import gzip
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
EXTRAS = os.path.join(HERE, "..", "yourwiki-kit", "extras")
SOURCES = ("1_namu_templates_200302.jsonl.gz", "2_alpha_templates_230104.jsonl.gz")

WS = re.compile(r"\s+")
CAT_TAIL = re.compile(r"(?:\s*\[\[분류:[^\]]*\]\])+\s*$")
NOINC = re.compile(r"<noinclude>.*?</noinclude>", re.S | re.I)
INCONLY = re.compile(r"</?includeonly>", re.I)
INCLUDE = re.compile(r"\[include\(([^()\[\]]*?)(?:,([^\[\]]*?))?\)\]")
PARAM = re.compile(r"@([^@\s=]+)(?:=([^@]*))?@")


def norm(s):
    """공백을 모두 지운 비교용 글."""
    return WS.sub("", s)


def load_templates(paths=None):
    """{제목: [원문 판들]} — 2020 덤프를 먼저, 알파위키를 나중에."""
    out = {}
    for name in paths or SOURCES:
        with gzip.open(os.path.join(EXTRAS, name), "rt", encoding="utf-8") as f:
            for line in f:
                d = json.loads(line)
                out.setdefault(d["title"], []).append(d["text"])
    return out


def clean_source(text):
    """틀 문서 원문 → 불러다 쓸 때 펼쳐지는 부분(noinclude 와 끝의 분류 달기는 뺀다)."""
    t = NOINC.sub("", text)
    t = INCONLY.sub("", t)
    t = CAT_TAIL.sub("", t)
    return t.strip()


def expand(title, tpls, args=None, depth=0, memo=None):
    """틀을 펼친다. 안에 든 [include(틀:…)] 도 펼치고 @매개변수@ 는 args 로 채운다. 못 펼치면 None."""
    if depth > 4 or title not in tpls:
        return None
    outs = []
    for src in tpls[title]:
        t = clean_source(src)

        def fill(m):
            v = (args or {}).get(m.group(1))
            return v if v is not None else (m.group(2) if m.group(2) is not None else m.group(0))
        t = PARAM.sub(fill, t)
        bad = []

        def inc(m):
            name = m.group(1).strip()
            sub = {}
            for i, part in enumerate((m.group(2) or "").split(","), 1):
                if "=" in part:
                    k, v = part.split("=", 1)
                    sub[k.strip()] = v.strip()
                elif part.strip():
                    sub[str(i)] = part.strip()
            r = expand(name, tpls, sub, depth + 1)
            if r is None:
                bad.append(name)
                return m.group(0)
            return r
        t = INCLUDE.sub(inc, t)
        if not bad:
            outs.append(t)
    return outs[0] if outs else None


def fixed_templates(tpls, min_len=20):
    """매개변수 없이 완전히 펼쳐지는 틀: {제목: [(펼친 글, 비교용 글), …]} (판이 둘이면 둘 다)."""
    out = {}
    for title in tpls:
        if not title.startswith("틀:") or "/" in title.split(":", 1)[1] and title.endswith("/설명문서"):
            continue
        vers = []
        for i in range(len(tpls[title])):
            sub = {title: [tpls[title][i]]}
            for k, v in tpls.items():
                if k != title:
                    sub[k] = v
            e = expand(title, sub)
            if e is None or PARAM.search(e):
                continue
            n = norm(e)
            if len(n) >= min_len and n not in [x[1] for x in vers]:
                vers.append((e, n))
        if vers:
            out[title] = vers
    return out


def build_index(fixed):
    """첫 줄(공백 없앤 것) → [(비교용 전체 글, 틀 제목)] (긴 것부터)."""
    idx = {}
    for title, vers in fixed.items():
        for e, n in vers:
            first = next((norm(ln) for ln in e.split("\n") if ln.strip()), "")
            if first:
                idx.setdefault(first, []).append((n, title))
    for v in idx.values():
        v.sort(key=lambda x: -len(x[0]))
    return idx


def refold_text(text, idx):
    """문서 글에서 틀 펼친 덩어리를 찾아 `[include(틀:이름)]` 로 바꾼다. (새 글, 바꾼 목록) 을 돌려준다."""
    lines = text.split("\n")
    nl = [norm(ln) for ln in lines]
    out, used, i = [], [], 0
    while i < len(lines):
        cand = idx.get(nl[i]) if nl[i] else None
        hit = None
        if cand:
            for n, title in cand:
                acc, j = "", i
                while j < len(lines) and len(acc) < len(n):
                    acc += nl[j]
                    j += 1
                if acc == n:
                    hit = (j, title, n)
                    break
        if hit:
            j, title, n = hit
            out.append(f"[include({title})]")
            used.append((title, len(n)))
            i = j
        else:
            out.append(lines[i])
            i += 1
    return "\n".join(out), used
