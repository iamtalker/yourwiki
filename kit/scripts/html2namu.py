"""나무위키 렌더링 HTML(2026-08 크롤링 덤프) → 나무마크 되돌리기.

본문 찾기·광고 제거·가짜 요소 제거는 namudump(MIT, hishydrogen)의 web/render.js 방식을 파이썬으로 옮겼다.
틀은 이미 펼쳐진 상태로 렌더링되어 있어 [include(...)] 로는 되돌리지 못하고, 펼쳐진 내용을 그대로 쓴다.

    namumark, info = convert(html_text, class_size_map)
"""
import html as htmlmod
import re
import urllib.parse
from html.parser import HTMLParser

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
BLOCK = {"div", "p", "table", "ul", "ol", "blockquote", "details", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "pre"}
FN_RE = re.compile(r"^fn-(.+)$")
SIZE_MAP = {"u1": "+1", "u2": "+2", "u3": "+3", "u4": "+4", "u5": "+5",
            "d1": "-1", "d2": "-2", "d3": "-3", "d4": "-4", "d5": "-5"}


# ---------------------------------------------------------------- 트리
class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag, attrs=None, parent=None):
        self.tag, self.attrs, self.children, self.parent = tag, attrs or {}, [], parent

    def cls(self):
        return self.attrs.get("class", "").split()

    def text(self):
        return "".join(c if isinstance(c, str) else c.text() for c in self.children)

    def iter(self):
        for c in self.children:
            if isinstance(c, Node):
                yield c
                yield from c.iter()

    def remove(self):
        if self.parent:
            self.parent.children = [c for c in self.parent.children if c is not self]
            self.parent = None

    def elements(self):
        return [c for c in self.children if isinstance(c, Node)]


class Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        n = Node(tag, {k: (v or "") for k, v in attrs}, self.cur)
        self.cur.children.append(n)
        if tag not in VOID:
            self.cur = n

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(Node(tag, {k: (v or "") for k, v in attrs}, self.cur))

    def handle_endtag(self, tag):
        n = self.cur
        while n is not self.root and n.tag != tag:
            n = n.parent
        if n is not self.root:
            self.cur = n.parent

    def handle_data(self, data):
        if self.cur.tag in ("script", "noscript", "style") and self.cur.tag != "style":
            return
        self.cur.children.append(data)


def parse(text):
    b = Builder()
    b.feed(text)
    return b.root


def blank(s):
    return not s.replace("\xa0", "").strip()


# ---------------------------------------------------------------- 정리 (namudump render.js 1~7단계)
def find_body(root):
    lic = None
    for n in root.iter():
        for c in n.children:
            if isinstance(c, str) and "이 저작물은" in c:
                lic = n
    if lic:
        while lic.parent and lic.parent.tag != "body" and lic.parent.text().lstrip().startswith("이 저작물은"):
            lic = lic.parent
        sibs = lic.parent.elements() if lic.parent else []
        i = sibs.index(lic) - 1 if lic in sibs else -1
        while i >= 0 and blank(sibs[i].text()) and not any(x.tag in ("img", "table") for x in sibs[i].iter()):
            i -= 1
        if i >= 0:
            return sibs[i]
    return root


def clean(body):
    # 가짜 div, 광고, 빈 껍데기
    for n in list(body.iter()):
        if n.tag == "div" and n.attrs.get("class") and not n.elements() and blank(n.text()):
            n.remove()
    def has_content(x):
        return any(y.tag in ("h1", "h2", "h3", "h4", "h5", "h6", "table", "details")
                   or y.attrs.get("id", "").startswith("fn-") for y in x.iter())

    def squash_len(x):
        return len(re.sub(r"[\s\xa0]+", "", x.text()))

    def is_ad_marker(x):
        # 광고 상자 표식: 클래스 이름과 글자가 같은 8글자 div (예: <div class="qlPVvAEK">qlPVvAEK</div>)
        c = x.attrs.get("class", "").strip()
        return x.tag == "div" and len(c) == 8 and " " not in c and x.text().strip() == c

    for n in list(body.iter()):
        if n.parent is None:
            continue
        if is_ad_marker(n) or any(isinstance(c, str) and c.strip() in ("파워링크", "광고등록") for c in n.children):
            # 광고 상자: 문단 제목·표·각주가 없고 짧은(2,000자 미만) 조상까지만 올라가 지운다.
            # (문서가 한 상자에 겹겹이 중첩된 경우가 있어 무작정 올라가면 본문까지 지워진다)
            e = n
            while (e.parent and e.parent is not body and not has_content(e.parent)
                   and squash_len(e.parent) < 2000):
                e = e.parent
            e.remove()
    for n in list(body.iter()):
        if n.tag in ("noscript", "script"):
            n.remove()


# ---------------------------------------------------------------- 스타일 → 나무마크 속성
def style_dict(s):
    d = {}
    for part in (s or "").split(";"):
        k, _, v = part.partition(":")
        if k.strip() and v.strip():
            d[k.strip().lower()] = v.strip()
    return d


RGB_RE = re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)")


def hexcolor(c):
    """rgb(255,0,0) → #ff0000. 반투명(rgba, 알파<1)은 그대로 둔다."""
    m = RGB_RE.fullmatch((c or "").strip())
    if not m or (m.group(4) is not None and float(m.group(4)) < 1):
        return c
    return "#%02x%02x%02x" % tuple(int(x) for x in m.groups()[:3])


def pair(light, dark):
    light, dark = hexcolor(light), hexcolor(dark)
    if not light:
        return ""
    if dark and dark != light:
        return f"{light},{dark}"
    return light


def cell_attrs(n, kind):
    """td → <bgcolor=..><color=..>... / table → <tablebgcolor=..>..."""
    st = style_dict(n.attrs.get("style"))
    dk = style_dict(n.attrs.get("data-dark-style"))
    out = []
    bg = st.get("background-color") or st.get("background")
    if bg and not bg.startswith(("linear-gradient", "url", "var(")) and bg != "transparent":
        out.append(f"<{'table' if kind == 'table' else ''}bgcolor={pair(bg, dk.get('background-color') or dk.get('background'))}>")
    col = st.get("color")
    if col and not col.startswith("var("):
        out.append(f"<{'table' if kind == 'table' else ''}color={pair(col, dk.get('color'))}>")
    if kind == "table":
        if st.get("width"):
            out.append(f"<tablewidth={st['width']}>")
        border = st.get("border") or ""
        m = re.search(r"(#[0-9a-fA-F]{3,8}|rgba?\([^)]*\)|[a-z]+)\s*$", border)
        if m and "solid" in border:
            dm = re.search(r"(#[0-9a-fA-F]{3,8}|rgba?\([^)]*\)|[a-z]+)\s*$", dk.get("border") or "")
            out.append(f"<tablebordercolor={pair(m.group(1), dm.group(1) if dm else '')}>")
    else:
        if st.get("width"):
            out.append(f"<width={st['width']}>")
        if st.get("height"):
            out.append(f"<height={st['height']}>")
        ta = st.get("text-align")
        if ta in ("center", "right", "left"):
            out.append({"center": "<:>", "right": "<)>", "left": "<(>"}[ta])
        va = st.get("vertical-align")
        if va in ("top", "bottom"):
            out.append("<^|1>" if va == "top" else "<v|1>")
        rs = n.attrs.get("rowspan")
        if rs and rs != "1":
            out[-1:] = out[-1:]  # 순서 유지
            out.insert(0, f"<|{rs}>")
    return "".join(out)


# ---------------------------------------------------------------- 나무마크 출력
class Emitter:
    def __init__(self, size_map, footnotes):
        self.size_map = size_map
        self.fn = footnotes

    def inline(self, n):
        """인라인 내용 → 문자열 (줄바꿈은 \n)"""
        out = []
        for c in n.children:
            if isinstance(c, str):
                out.append(c.replace("\xa0", " "))
            else:
                out.append(self.node(c))
        return "".join(out)

    def node(self, n):
        t = n.tag
        if t in ("style", "script", "noscript", "button", "input"):
            return ""
        if t == "br":
            return "\n"
        if t == "hr":
            return "\n----\n"
        if t in ("strong", "b"):
            return wrap(self.inline(n), "'''")
        if t in ("em", "i"):
            return wrap(self.inline(n), "''")
        if t in ("del", "s"):
            return wrap(self.inline(n), "~~")
        if t == "u":
            return wrap(self.inline(n), "__")
        if t == "sup":
            return wrap(self.inline(n), "^^")
        if t == "sub":
            return wrap(self.inline(n), ",,")
        if t == "a":
            return self.link(n)
        if t == "img":
            return self.image(n)
        if t == "span":
            return self.span(n)
        if t == "lite-youtube":
            return f"[youtube({n.attrs.get('videoid', '')})]"
        if t == "iframe":
            src = n.attrs.get("src", "")
            m = re.search(r"youtube(?:-nocookie)?\.com/embed/([\w-]+)", src)
            return f"[youtube({m.group(1)})]" if m else (f"[[{src}]]" if src else "")
        if t in ("h1", "h2", "h3", "h4", "h5", "h6"):
            return self.heading(n)
        if t == "table":
            return self.table(n)
        if t in ("ul", "ol"):
            return self.list(n, 1)
        if t == "blockquote":
            body = self.inline(n).strip("\n")
            return "\n" + "\n".join("> " + l for l in body.split("\n")) + "\n"
        if t == "details":
            return self.folding(n)
        if t == "div":
            return self.div(n)
        return self.inline(n)

    def div(self, n):
        st = n.attrs.get("style", "").strip()
        body = self.inline(n)
        if re.search(r"display\s*:\s*inline", st) and "{{{#!" not in body and "||" not in body:
            # 줄 안에 들어가는 작은 상자(국기 아이콘 등): 블록으로 감싸면 링크·표 안에서 깨지므로 내용만.
            # 접기·표가 든 상자(틀의 접기 단추 줄 등)는 블록으로 둔다.
            return " ".join(x.strip() for x in body.split("\n") if x.strip())
        if st and not blank(body):
            dk = n.attrs.get("data-dark-style", "").strip()
            extra = f' dark-style="{dk}"' if dk else ""
            return f'\n{{{{{{#!wiki style="{st}"{extra}\n{body.strip(chr(10))}\n}}}}}}\n'
        if not body.startswith("\n"):
            body = "\n" + body
        return body

    def span(self, n):
        if n.attrs.get("id", "").startswith(("fn-", "rfn-")):
            return ""
        body = self.inline(n)
        if blank(body):
            return body
        st = style_dict(n.attrs.get("style"))
        dk = style_dict(n.attrs.get("data-dark-style"))
        col = st.get("color")
        if col and not col.startswith("var("):
            body = f"{{{{{{{pair(col, dk.get('color'))} {body}}}}}}}"
        for c in n.cls():
            v = self.size_map.get(c)
            if v and v in SIZE_MAP:
                body = f"{{{{{{{SIZE_MAP[v]} {body}}}}}}}"
                break
        return body

    def link(self, n):
        href = n.attrs.get("href", "")
        label = self.inline(n)
        if href.startswith("#fn-"):
            key = urllib.parse.unquote(href[4:])
            content = self.fn.get(key, "")
            name = "" if key.isdigit() else key
            return f"[*{name} {content}]" if content else f"[*{name}]"
        if href.startswith("#"):
            return label
        if href.startswith("/w/"):
            path = href[3:]
            anchor = ""
            if "#" in path:
                path, anchor = path.split("#", 1)
            title = urllib.parse.unquote(path.split("?")[0])
            target = title + ("#" + urllib.parse.unquote(anchor) if anchor else "")
            if title.startswith("파일:"):
                return label
            return f"[[{target}]]" if label.strip() == target else f"[[{target}|{label}]]"
        if href.startswith("/jump/"):
            return label
        if href.startswith(("http://", "https://")):
            return f"[[{href}]]" if label.strip() == href else f"[[{href}|{label}]]"
        return label

    def image(self, n):
        """[[파일:이름.확장자|width=..]]

        HTML 에는 파일 이름이 없다. 나무위키는 alt 기본값으로 '확장자 뺀 파일 이름'을 쓰므로
        alt + 이미지 주소의 확장자로 복원한다. 크기는 이미지를 감싼 바깥 span 의 width 이다.
        """
        src = n.attrs.get("data-src") or n.attrs.get("src", "")
        if src.startswith("data:"):
            return ""
        ext = re.search(r"\.(\w{2,5})(?:\?|$)", src)
        alt = n.attrs.get("alt") or "이미지"
        name = "파일:" + alt + ("." + ext.group(1) if ext and not alt.lower().endswith("." + ext.group(1).lower()) else "")
        width = ""
        p, hops = n.parent, 0
        while p is not None and p.tag == "span" and hops < 2:
            w = style_dict(p.attrs.get("style")).get("width", "")
            if w:
                width = w
            p, hops = p.parent, hops + 1
        width = width.replace("px", "")
        return f"[[{name}{'|width=' + width if width else ''}]]"

    def heading(self, n):
        level = int(n.tag[1])
        parts = []
        for c in n.children:
            if isinstance(c, Node) and c.tag == "a" and c.attrs.get("id", "").startswith("s-"):
                continue
            parts.append(c if isinstance(c, str) else self.node(c))
        text = re.sub(r"\[편집\]\s*$", "", "".join(parts)).strip()
        eq = "=" * level
        return f"\n{eq} {text} {eq}\n"

    def folding(self, n):
        summ = next((c for c in n.elements() if c.tag == "summary"), None)
        title = self.inline(summ).strip() if summ else "[ 펼치기 · 접기 ]"
        body = "".join(c if isinstance(c, str) else ("" if c is summ else self.node(c)) for c in n.children)
        return f"\n{{{{{{#!folding {title}\n{body.strip(chr(10))}\n}}}}}}\n"

    def list(self, n, depth):
        out = []
        ordered = n.tag == "ol"
        for li in n.elements():
            if li.tag != "li":
                continue
            text, subs = [], []
            for c in li.children:
                if isinstance(c, Node) and c.tag in ("ul", "ol"):
                    subs.append(self.list(c, depth + 1))
                else:
                    text.append(c if isinstance(c, str) else self.node(c))
            line = " ".join(x.strip() for x in "".join(text).split("\n") if x.strip())
            out.append(" " * depth + ("1. " if ordered else "* ") + line)
            out.extend(s.strip("\n") for s in subs)
        return "\n" + "\n".join(out) + "\n"

    def table(self, n):
        rows = [r for r in n.iter() if r.tag == "tr" and closest(r, "table") is n]
        tattr = cell_attrs(n, "table")
        wrapper = n.parent
        if wrapper is not None and wrapper.tag == "div":
            ws = style_dict(wrapper.attrs.get("style"))
            if ws.get("width") and "tablewidth" not in tattr:
                tattr += f"<tablewidth={ws['width']}>"
        lines = []
        for ri, r in enumerate(rows):
            cells = [c for c in r.elements() if c.tag in ("td", "th")]
            line = ""
            for ci, c in enumerate(cells):
                span = int(c.attrs.get("colspan") or 1)
                attrs = (tattr if ri == 0 and ci == 0 else "") + cell_attrs(c, "cell")
                body = self.inline(c).strip("\n")
                body = re.sub(r"\n{2,}", "\n", body)
                line += "||" * span + attrs + " " + body + " "
            lines.append(line + "||")
        return "\n" + "\n".join(lines) + "\n"


def closest(n, tag):
    p = n.parent
    while p is not None and p.tag != tag:
        p = p.parent
    return p


def wrap(s, mark):
    if blank(s):
        return s
    # 글자 꾸미기는 줄을 넘지 못하므로 안쪽 줄바꿈은 [br] 로
    s = s.strip("\n").replace("\n", "[br]")
    return f"{mark}{s}{mark}"


def collect_footnotes(body, em):
    """아래쪽 각주 목록(span#fn-N)을 모아 {이름: 내용} 으로 만들고 목록은 지운다."""
    fns = {}
    for s in list(body.iter()):
        m = FN_RE.match(s.attrs.get("id", "")) if s.tag == "span" else None
        if not m or s.parent is None or s.parent.tag != "span":
            continue
        item = s.parent
        parts = []
        for c in item.children:
            if c is s or (isinstance(c, Node) and c.tag == "a" and c.attrs.get("href", "").startswith("#rfn-")):
                continue
            parts.append(c)
        tmp = Node("span")
        tmp.children = parts
        fns[urllib.parse.unquote(m.group(1))] = em.inline(tmp).strip().replace("\n", " ")
        lst = item.parent
        item.remove()
        if lst is not None and lst.tag == "div" and not lst.elements() and blank(lst.text()):
            lst.remove()
    return fns


def _category_titles(n):
    out = []
    for a in n.iter():
        href = a.attrs.get("href", "") if a.tag == "a" else ""
        if href.startswith("/w/"):
            out.append(urllib.parse.unquote(href[3:].split("#")[0]))
    return out


def extract_categories(body):
    """'분류' 글자와 분류 링크만 든 가장 작은 상자를 찾아 떼어 낸다.

    상자가 본문까지 감싸는 경우(제목·표가 들어 있음)에는 글자와 목록만 지운다.
    """
    for n in list(body.iter()):
        if n.parent is None or n.elements() or n.text().strip() != "분류":
            continue
        box = n.parent
        ul = None
        while box is not None and box is not body:
            ul = next((u for u in box.iter() if u.tag == "ul"
                       and _category_titles(u) and all(t.startswith("분류:") for t in _category_titles(u))), None)
            if ul:
                break
            box = box.parent
        if not ul:
            continue
        cats = [t for t in _category_titles(ul) if t.startswith("분류:")]
        if any(x.tag in ("h1", "h2", "h3", "h4", "h5", "h6", "table") for x in box.iter()):
            ul.remove()
            n.remove()
        else:
            box.remove()
        return cats
    return []


def replace_toc(body):
    links = [a for a in body.iter() if a.tag == "a" and a.attrs.get("href", "").startswith("#s-")
             and a.parent is not None and a.parent.tag == "span"]
    if not links:
        return False
    box = links[0].parent.parent
    squash = lambda s: re.sub(r"[\s\xa0]+", "", s)  # noqa: E731
    toc_text = squash(box.text())
    for _ in range(6):
        p = box.parent
        # 목차 글자만 든 껍데기일 때만 올라간다 (상자가 겹겹이 중첩돼 본문까지 감싸는 경우가 있음)
        if p is None or p is body or squash(p.text()) != toc_text:
            break
        box = p
    marker = Node("#toc", parent=box.parent)
    if box.parent:
        box.parent.children = [marker if c is box else c for c in box.parent.children]
    return True


class TocEmitter(Emitter):
    def node(self, n):
        if n.tag == "#toc":
            return "\n[목차]\n"
        return super().node(n)


H1_RE = re.compile(r"<h1\b[^>]*>(.*?)</h1>", re.S)
NOREDIRECT_RE = re.compile(r'href="/w/([^"?]*)\?noredirect=1"')


def redirect_target(raw, title):
    """넘겨주기 문서면 대상 제목을 돌려준다.

    크롤러가 넘겨주기 주소로 들어가면 나무위키는 대상 문서를 보여 주며
    '<a href="/w/원래제목?noredirect=1">원래제목</a>에서 넘어옴' 안내를 붙인다. 대상 제목은 h1 에 있다.
    """
    if not title or "에서 넘어옴" not in raw:
        return None
    # 특수 문자를 주소로 바꾸는 방식이 나무위키와 다를 수 있어 풀어서 비교한다
    for m in NOREDIRECT_RE.finditer(raw):
        if (urllib.parse.unquote(htmlmod.unescape(m.group(1))) == title
                and "에서 넘어옴" in raw[m.end():m.end() + 600]):
            break
    else:
        return None
    m = H1_RE.search(raw)
    if not m:
        return None
    target = htmlmod.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
    return target if target and target != title else None


def convert(raw, size_map=None, title=None):
    target = redirect_target(raw, title)
    if target:
        return f"#redirect {target}\n", {"categories": [], "toc": False, "redirect": target}
    raw = re.sub(r'<a\b[^>]*\bhref="/jump/[^"]*"[^>]*>\s*</a>', "", raw)
    root = parse(raw)
    body = find_body(root)
    clean(body)
    cats = extract_categories(body)
    had_toc = replace_toc(body)
    em = TocEmitter(size_map or {}, {})
    em.fn = collect_footnotes(body, em)
    text = em.inline(body)
    text = htmlmod.unescape(text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip("\n")
    head = "".join(f"[[{c}]]" for c in cats)
    return (head + "\n" + text if head else text) + "\n", {"categories": cats, "toc": had_toc}
