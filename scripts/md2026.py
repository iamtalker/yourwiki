"""2026-08 크롤링 덤프(namu-md.sqlite)의 Markdown 문서 한 건을 openNAMU 용 HTML 로 바꾼다.

덤프는 나무위키 화면을 긁어 Markdown 으로 바꾼 것이라 다음을 손봐야 한다.
- 화면에 섞여 있던 나무위키 광고(파워링크 문구·광고 이미지)를 걷어낸다.
  광고 구역은 의미 없는 8글자 표식 줄(예: qlPVvAEK)이나 광고 이미지 줄로 시작하고,
  '[문구](#s-N)' 형태의 줄로만 채워져 있다.
- 문서 링크(xxx.md)는 위키 주소(/w/xxx)로 바꾼다. 분류는 '분류_' 로 저장돼 있다.
- 이미지는 나무위키 서버에서 불러오므로, 누를 때만 열리는 링크로 바꾼다(이미지는 다루지 않음).
- HTML 이 그대로 화면에 나가므로 스크립트·이벤트 속성·javascript: 링크를 없앤다.
"""
import html as htmlmod
import re
import urllib.parse

import markdown  # python-markdown (BSD-3), 설치 때 tools/pylib 에 받음

FRONT_RE = re.compile(r"\A---\n(.*?)\n---\n", re.S)
MARKER_RE = re.compile(r"^[A-Za-z0-9+/=]{8}$")
ANCHOR_LINE_RE = re.compile(r"^(?:\[[^\]]*\]\(#s-\d+\)\s*)+$")
AD_IMAGE_RE = re.compile(r"^\[!\[[^\]]*\]\(//i\.namu\.wiki/i/[^)]*\)\]\([^)]*\)\s*$")
MD_LINK_RE = re.compile(r'\]\((?!https?:|//|#|/)([^)\s]+?)\.md(?:\s+"[^"]*")?\)')
IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(((?:https?:)?//[^)\s]+)[^)]*\)")
JUMP_RE = re.compile(r"\]\(/jump/")
UNSAFE_TAG_RE = re.compile(r"<\s*(script|style|object|embed)\b.*?(?:</\s*\1\s*>|/?>)", re.I | re.S)
ON_ATTR_RE = re.compile(r"\s+on[a-z]+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.I)
JS_URL_RE = re.compile(r"(href|src)\s*=\s*([\"'])\s*javascript:[^\"']*\2", re.I)

_md = markdown.Markdown(extensions=["extra", "sane_lists"], output_format="html")


def parse_front(md):
    m = FRONT_RE.match(md)
    if not m:
        return {}, md
    meta = {}
    for line in m.group(1).split("\n"):
        k, _, v = line.partition(":")
        meta[k.strip()] = v.strip().strip('"')
    return meta, md[m.end():]


def strip_ads(body):
    """광고 구역을 걷어낸다. 걷어낸 줄 수를 함께 돌려준다."""
    lines = body.split("\n")
    drop = [False] * len(lines)
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if MARKER_RE.match(s) or AD_IMAGE_RE.match(s):
            j = i + 1
            while j < len(lines):
                t = lines[j].strip()
                if not t or ANCHOR_LINE_RE.match(t) or AD_IMAGE_RE.match(t):
                    j += 1
                elif MARKER_RE.match(t):
                    j += 1
                    break
                else:
                    break
            for k in range(i, j):
                drop[k] = True
            i = j
        else:
            i += 1
    kept = [l for l, d in zip(lines, drop) if not d]
    return "\n".join(kept), sum(drop)


def wiki_link(m):
    target = urllib.parse.unquote(m.group(1))
    if target.startswith("분류_"):
        target = "분류:" + target[3:]
    return "](/w/" + urllib.parse.quote(target, safe="") + ")"


def image_link(m):
    alt = m.group(1) or "이미지"
    url = m.group(2)
    if url.startswith("//"):
        url = "https:" + url
    return f"[🖼 {alt} (원본 이미지)]({url})"


def sanitize(h):
    h = UNSAFE_TAG_RE.sub("", h)
    h = ON_ATTR_RE.sub("", h)
    return JS_URL_RE.sub(r'\1=\2#\2', h)


def attribution(meta, title):
    src = meta.get("source") or "https://namu.wiki/w/" + urllib.parse.quote(title, safe="")
    when = meta.get("last_modified", "")
    return (
        '<hr><div class="kit-attribution" style="font-size:0.9em;color:#666">'
        f'출처: <a href="{htmlmod.escape(src)}" target="_blank" rel="noopener">나무위키 「{htmlmod.escape(title)}」 문서</a>'
        f" (최근 수정 {htmlmod.escape(when)}, 2026-08 크롤링 덤프) · "
        '라이선스: <a href="https://creativecommons.org/licenses/by-nc-sa/2.0/kr/" target="_blank" rel="noopener">'
        "CC BY-NC-SA 2.0 KR</a> · 저작권은 각 기여자에게 있으며, 기여자 목록은 원 문서의 역사에서 볼 수 있습니다.</div>"
    )


def convert(md_text, title):
    """Markdown 문서 → (html, meta, 걷어낸 광고 줄 수)"""
    meta, body = parse_front(md_text)
    body, ad_lines = strip_ads(body)
    body = re.sub(r"\A\s*# [^\n]*\n", "", body, count=1)  # 맨 위 제목은 openNAMU 가 따로 보여 준다
    body = MD_LINK_RE.sub(wiki_link, body)
    body = IMAGE_RE.sub(image_link, body)
    body = JUMP_RE.sub("](https://namu.wiki/jump/", body)
    _md.reset()
    html = sanitize(_md.convert(body))
    return html + attribution(meta, title), meta, ad_lines
