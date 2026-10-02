"""문서 글에서 '틀일 수 있는 덩어리'를 자른다(되접기의 모든 단계가 이 규칙 하나를 쓴다).

덩어리 = 문서 맨 바깥 층의 완결된 표 하나, 또는 `{{{#!` 로 시작해 괄호가 맞는 상자 하나.
 - 표: 줄머리 `||` 로 시작하고, 여러 줄에 걸친 칸(`{{{#!wiki` 상자·줄바꿈이 든 칸)을 끝까지 포함해, 마지막 줄이 `||` 로 끝나야 한다.
 - 열린 표 행의 한가운데나 괄호 안쪽에서는 시작하지 않는다(표를 반으로 가르지 않으려고).
"""


def scan_state(lines):
    """줄마다 (그 줄 앞의 괄호 깊이, 그 줄 앞에 열려 있는 표 행인가)."""
    depth, open_row = 0, False
    dep, opn = [], []
    for ln in lines:
        dep.append(depth)
        opn.append(open_row)
        depth = max(0, depth + ln.count("{{{") - ln.count("}}}"))
        s = ln.rstrip()
        if open_row:
            if s.endswith("||") and depth == 0:
                open_row = False
        elif ln.startswith("||") and not (s.endswith("||") and depth == 0):
            open_row = True
    return dep, opn


def unit_spans(lines, max_lines=400):
    """(시작 줄, 끝 줄+1) 들을 차례로 돌려준다."""
    dep, opn = scan_state(lines)
    n, i = len(lines), 0
    while i < n:
        ln = lines[i]
        if dep[i] != 0 or opn[i]:
            i += 1
            continue
        if ln.startswith("{{{#!"):
            d, j = 0, i
            while j < n:
                d += lines[j].count("{{{") - lines[j].count("}}}")
                j += 1
                if d <= 0:
                    break
            if d <= 0 and j - i <= max_lines:
                yield i, j
                i = j
                continue
        elif ln.startswith("||"):
            j = i
            while j < n and ((lines[j].startswith("||") and dep[j] == 0) or opn[j]):
                j += 1
            # 마지막 줄이 `||` 로 끝나고 행이 닫혀 있어야 완결된 표
            if j - i <= max_lines and lines[j - 1].rstrip().endswith("||") and (j >= n or not opn[j]):
                yield i, j
                i = j
                continue
            i = max(j, i + 1)
            continue
        i += 1


def units(text):
    lines = text.split("\n")
    for i, j in unit_spans(lines):
        yield "\n".join(lines[i:j])


import re  # noqa: E402

CAT_LINK = re.compile(r"\[\[분류:[^\[\]\n]*\]\]")


TOKEN = re.compile(r"\{\{\{|\}\}\}|\[\[분류:[^\[\]\n]*\]\]")


def category_links(text):
    """덩어리 안에서 원본 렌더링 때 실제로 적용되던 [[분류:…]] 링크만 이어 붙인 글.

    openNAMU 는 (1) 불러 온 틀 안의 분류를 문서에 적용하지 않으므로, 덩어리를 틀 호출로 바꿀 때 문서에 남겨야 분류가 유지된다.
    (2) `{{{#!wiki}}}` 상자·접기 안의 분류는 원본에서도 적용되지 않았으므로 남기지 않는다(괄호 깊이 0 의 것만).
    (3) 표시 글자에 `{{{ }}}` 서식이 든 분류 링크는 문서 바깥에 두면 렌더러가 깨지므로 표시 글자를 뗀다."""
    out, depth = [], 0
    for m in TOKEN.finditer(text):
        tok = m.group(0)
        if tok == "{{{":
            depth += 1
        elif tok == "}}}":
            depth = max(0, depth - 1)
        elif depth == 0:
            body = tok[2:-2]
            if "|" in body:
                target, label = body.split("|", 1)
                if "{" in label or "}" in label:
                    body = target
            out.append("[[" + body + "]]")
    return "".join(out)
