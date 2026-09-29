"""내보내기: 유어위키 DB(나무마크)를 다른 위키 엔진이 읽는 형식으로 바꾼다 (유어위키 1.2, 표준 라이브러리만 사용).

  MediaWiki → 가져오기용 XML(.xml.gz). MediaWiki 의 maintenance/importDump.php 로 넣는다.
  DokuWiki  → data/pages/ 폴더를 담은 .zip. DokuWiki 폴더에 풀고 bin/indexer.php 로 색인을 만든다.
  Markdown  → 문서마다 .md 파일 하나(GitHub 방식 마크다운)를 담은 .zip. Obsidian·GitHub·정적 사이트 도구에서 연다.

나무마크의 흔한 문법(문단, 굵게·기울임, 링크, 분류, 표, 목록, 각주, 접기, 문법 강조, 틀 불러오기 등)을 옮긴다.
옮길 수 없는 것(#!html, 이미지 등)은 빼고, 모든 문서 끝의 출처·라이선스 고지는 그대로 남긴다.

사용: python convert_wiki.py <wiki 폴더> --to mediawiki|dokuwiki|markdown [--out 파일] [--limit N]
"""
import argparse
import datetime
import gzip
import html
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import zipfile

NOTICE = ("이 데이터는 나무위키(https://namu.wiki) 문서를 바탕으로 하며 CC BY-NC-SA 2.0 KR 라이선스를 따릅니다.\n"
          "저작권은 각 문서의 기여자에게 있고, 각 문서 끝에 원 문서 주소와 라이선스가 적혀 있습니다. 지우지 마세요.\n"
          "상업적 이용은 금지됩니다. 이 데이터로 만든 결과물도 같은 라이선스로 공개해야 합니다.\n"
          "https://creativecommons.org/licenses/by-nc-sa/2.0/kr/\n")
SKIP_PREFIX = ("파일:", "휴지통:", "파일휴지통:")
PH = "\x00"  # 자리표시 기호(변환이 끝나면 되돌린다)
PH_RE = re.compile(PH + r"(\d+)" + PH)
CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
COLOR_RE = re.compile(r"#?(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[a-zA-Z]+)$")


def css_color(v):
    v = v.split(",")[0].strip()
    if not COLOR_RE.match(v):
        return ""
    return v if v.startswith("#") or not re.fullmatch(r"[0-9a-fA-F]{3}|[0-9a-fA-F]{6}", v) else "#" + v


# ---------------------------------------------------------------- 제목
def mw_title(t):
    if t.startswith("category:"):
        t = "Category:" + t[9:]
    elif t.startswith("분류:"):
        t = "Category:" + t[3:]
    elif t.startswith("틀:"):
        t = "Template:" + t[2:]
    # MediaWiki 제목에 쓸 수 없는 글자는 비슷한 전각 글자로
    return t.translate(str.maketrans("#<>[]|{}", "＃＜＞［］｜｛｝")).strip() or "_"


def doku_id(t):
    """DokuWiki 문서 ID. 분류·틀은 이름공간으로, 나머지 제목의 ':' 는 '_' 로."""
    ns = ""
    if t.startswith("category:"):
        ns, t = "분류:", t[9:]
    else:
        for p in ("분류:", "틀:", "유어위키:"):
            if t.startswith(p):
                ns, t = p, t[len(p):]
                break
    t = t.lower().replace(":", "_").replace("/", "_").replace(";", "_")
    t = re.sub(r"[\s\x00-\x1f!\"#$%&'()*+,<=>?@\[\\\]^`{|}~]+", "_", t)
    t = re.sub(r"_+", "_", t).strip("_.") or "_"
    return ns + t


def doku_path(pid):
    parts = pid.split(":")
    return "data/pages/" + "/".join(urllib.parse.quote(p, safe="") for p in parts) + ".txt"


MD_BAD = str.maketrans('\\/:*?"<>|#', "＼／：＊？＂＜＞｜＃")
WIN_RESERVED = {"con", "prn", "aux", "nul"} | {f"{p}{i}" for p in ("com", "lpt") for i in range(1, 10)}


def md_name(t):
    """마크다운 파일 이름. 윈도에서 못 쓰는 글자는 전각 글자로 바꾸고, 너무 길면 줄인다."""
    if t.startswith("category:"):
        t = "분류:" + t[9:]
    name = t.translate(MD_BAD).strip().rstrip(".") or "_"
    if name.lower().split(".")[0] in WIN_RESERVED:
        name += "_"
    if len(name.encode("utf-8")) > 200:
        import hashlib
        name = name.encode("utf-8")[:180].decode("utf-8", "ignore") + "~" + hashlib.sha1(t.encode()).hexdigest()[:8]
    return name + ".md"


def resolve(cur, target):
    """나무마크 상대 링크(/하위, ../)를 풀고, 문단 번호(#s-1) 같은 꼬리를 나눈다."""
    anchor = ""
    if "#" in target and not target.startswith("#"):
        target, anchor = target.split("#", 1)
    elif target.startswith("#"):
        return cur, target[1:]
    if target.startswith("../"):
        base = cur
        while target.startswith("../"):
            base, target = base.rsplit("/", 1)[0] if "/" in base else base, target[3:]
        target = base + ("/" + target if target else "")
    elif target.startswith("/"):
        target = cur + target
    if anchor.startswith("s-"):
        anchor = ""
    return target.strip(), anchor


# ---------------------------------------------------------------- 변환기
class Conv:
    def __init__(self, target, title):
        self.mw = target == "mediawiki"
        self.md = target == "markdown"
        self.doku = target == "dokuwiki"
        self.notes = []
        self.title = title
        self.ph = []
        self.cats = []
        self.refs = 0
        self.has_refs_macro = False

    def put(self, s):
        self.ph.append(s)
        return f"{PH}{len(self.ph) - 1}{PH}"

    def unph(self, s):
        for _ in range(20):
            n = PH_RE.sub(lambda m: self.ph[int(m.group(1))], s)
            if n == s:
                break
            s = n
        return s.replace(PH, "")

    # ---------------- {{{ }}} 블록
    def braces(self, text):
        out, i = [], 0
        while True:
            j = text.find("{{{", i)
            if j < 0:
                out.append(text[i:])
                return "".join(out)
            out.append(text[i:j])
            k, depth = j + 3, 1
            while depth and k < len(text):
                if text.startswith("{{{", k):
                    depth, k = depth + 1, k + 3
                elif text.startswith("}}}", k):
                    depth, k = depth - 1, k + 3
                else:
                    k += 1
            if depth:  # 닫히지 않음: 그대로 둔다
                out.append(text[j:])
                return "".join(out)
            out.append(self.brace(text[j + 3:k - 3]))
            i = k

    def brace(self, inner):
        multi = "\n" in inner
        head, _, body = inner.partition("\n") if multi else (inner, "", "")
        h = head.strip()
        low = h.lower()
        if low.startswith("#!syntax"):
            lang = h[8:].strip() or "text"
            code = body if multi else ""
            if self.mw:
                return self.put(f'<syntaxhighlight lang="{html.escape(lang)}">\n{code}\n</syntaxhighlight>')
            if self.md:
                return self.put(f"\n```{re.sub(r'[^a-zA-Z0-9+#-]', '', lang)}\n{code}\n```\n")
            return self.put(f"<code {re.sub(r'[^a-z0-9+#-]', '', lang.lower()) or 'text'}>\n{code}\n</code>")
        if low.startswith("#!html"):
            return self.put("<!-- #!html 은 옮기지 않음 -->" if self.mw else "")
        if low.startswith("#!folding"):
            title = self.inline(self.braces(h[9:].strip() or "펼치기 · 접기"))
            content = self.block(self.braces(body))
            if self.mw:
                return self.put(f'\n<div class="mw-collapsible mw-collapsed">\n{title}\n'
                                f'<div class="mw-collapsible-content">\n{content}\n</div></div>\n')
            if self.md:
                return self.put(f"\n<details><summary>{title}</summary>\n\n{content}\n\n</details>\n")
            return self.put(f"\n**{title}**\n\n{content}\n")
        if low.startswith("#!wiki"):
            m = re.search(r'style\s*=\s*"([^"]*)"', h)
            content = self.block(self.braces(body if multi else ""))
            if self.mw:
                style = html.escape(m.group(1)) if m else ""
                return self.put(f'\n<div style="{style}">\n{content}\n</div>\n')
            return self.put(f"\n{content}\n")
        m = re.match(r"([+-])([1-5])\s", inner)
        if m:
            content = self.inline(self.braces(inner[m.end():]))
            if self.mw:
                size = {"+": ["", "1.2em", "1.4em", "1.6em", "1.8em", "2em"],
                        "-": ["", ".9em", ".8em", ".7em", ".6em", ".5em"]}[m.group(1)][int(m.group(2))]
                return self.put(f'<span style="font-size:{size}">{content}</span>')
            return self.put(content)
        m = re.match(r"#([0-9a-zA-Z]+(?:,#?[0-9a-zA-Z]+)?)\s", inner)
        if m and css_color(m.group(1)):
            content = self.inline(self.braces(inner[m.end():])) if not multi else self.block(self.braces(inner[m.end():]))
            if self.mw:
                return self.put(f'<span style="color:{css_color(m.group(1))}">{content}</span>')
            return self.put(content)
        # 문자 그대로
        if multi:
            text = inner[1:] if inner.startswith("\n") else inner
            if self.mw:
                return self.put("<pre>" + html.escape(text, quote=False) + "</pre>")
            if self.md:
                fence = "````" if "```" in text else "```"
                return self.put(f"\n{fence}\n{text}\n{fence}\n")
            return self.put("<code>\n" + text + "\n</code>")
        if self.mw:
            return self.put("<code><nowiki>" + inner.replace("</nowiki>", "&lt;/nowiki>") + "</nowiki></code>")
        if self.md:
            return self.put(f"`` {inner} ``" if "`" in inner else f"`{inner}`")
        return self.put("''%%" + inner.replace("%%", "% %") + "%%''")

    # ---------------- 각주 [* ...]
    def footnotes(self, text):
        out, i = [], 0
        while True:
            j = text.find("[*", i)
            if j < 0:
                out.append(text[i:])
                return "".join(out)
            out.append(text[i:j])
            k, depth = j + 1, 1
            while depth and k < len(text):
                c = text[k]
                depth += c == "["
                depth -= c == "]"
                k += 1
            if depth:
                out.append(text[j:])
                return "".join(out)
            inner = text[j + 2:k - 1]
            name, _, body = inner.partition(" ")
            if not _:
                name, body = inner, ""
            body = self.inline(self.footnotes(body))
            self.refs += 1
            if self.mw:
                nm = f' name="{html.escape(name)}"' if name else ""
                out.append(self.put(f"<ref{nm}>{body}</ref>" if body else f"<ref{nm} />"))
            elif self.md:
                fid = re.sub(r"[^\w가-힣-]", "_", name) if name else str(self.refs)
                if body:
                    self.notes.append((fid, body))
                out.append(self.put(f"[^{fid}]"))
            else:
                out.append(self.put(f"(({body or name}))"))
            i = k

    # ---------------- 줄 안의 문법
    def link(self, m):
        inner = m.group(1)
        target, _, label = inner.partition("|")
        target = target.strip()
        if re.match(r"https?://", target):
            label = self.inline(label) if label else target
            if self.md:
                return self.put(f"[{label}]({target})")
            return self.put(f"[{target} {label}]" if self.mw else f"[[{target}|{label}]]")
        colon = target.startswith(":")
        if colon:
            target = target[1:]
        if target.startswith(("파일:", "이미지:")):
            return ""
        if target.startswith("분류:") and not colon:
            name = target[3:].split("#")[0].strip()
            if name:
                self.cats.append(name)
            return ""
        if target.startswith("분류:"):
            target = "category:" + target[3:]
        page, anchor = resolve(self.title, target)
        text = self.inline(label) if label else (page if not anchor else f"{page}#{anchor}")
        if self.mw:
            t = mw_title(page)
            if t.startswith("Category:"):
                t = ":" + t
            return self.put(f"[[{t}{'#' + anchor if anchor else ''}|{text}]]")
        if self.md:
            return self.put(f"[{text}](<{md_name(page)}>)")
        return self.put(f"[[{doku_id(page)}{'#' + anchor if anchor else ''}|{text}]]")

    def macro(self, m):
        name, arg = m.group(1).lower(), (m.group(3) or "").strip()
        mw = self.mw
        if name in ("목차", "tableofcontents"):
            return self.put("__TOC__") if mw else ""
        if name in ("각주", "footnote"):
            self.has_refs_macro = True
            return self.put("<references />") if mw else ""
        if name == "br":
            return self.put("<br />" if mw else "<br>" if self.md else "\\\\ ")
        if name == "clearfix":
            return ""
        if name == "include":
            parts = [p.strip() for p in arg.split(",")]
            tpl = parts[0]
            params = [p for p in parts[1:] if p]
            if mw:
                t = tpl[2:] if tpl.startswith("틀:") else ":" + mw_title(tpl)
                return self.put("{{" + "|".join([t] + params) + "}}")
            if self.md:
                return self.put(f"[{tpl}](<{md_name(tpl)}>)")
            return self.put(f"[[{doku_id(tpl)}|{tpl}]]")
        videos = {"youtube": ("유튜브", "https://www.youtube.com/watch?v={}"),
                  "nicovideo": ("니코니코 동화", "https://www.nicovideo.jp/watch/{}"),
                  "vimeo": ("비메오", "https://vimeo.com/{}"),
                  "kakaotv": ("카카오TV", "https://tv.kakao.com/v/{}"),
                  "navertv": ("네이버TV", "https://tv.naver.com/v/{}")}
        if name in videos:
            vid = arg.split(",")[0].strip()
            label, fmt = videos[name]
            url = fmt.format(urllib.parse.quote(vid))
            if self.md:
                return self.put(f"[▶ {label}에서 보기]({url})")
            return self.put(f"[{url} ▶ {label}에서 보기]" if mw else f"[[{url}|▶ {label}에서 보기]]")
        if name in ("age", "dday"):
            try:
                d = datetime.date.fromisoformat(arg)
                today = datetime.date.today()
                if name == "age":
                    v = today.year - d.year - ((today.month, today.day) < (d.month, d.day))
                else:
                    v = (today - d).days
                    v = f"+{v}" if v > 0 else str(v)
                return str(v)
            except ValueError:
                return arg
        if name in ("date", "datetime"):
            return time.strftime("%Y-%m-%d %H:%M:%S" if name == "datetime" else "%Y-%m-%d")
        if name == "pagecount":
            return ""
        if name == "anchor":
            return self.put(f'<span id="{html.escape(arg)}"></span>') if mw else ""
        if name == "math":
            return self.put(f"<math>{arg}</math>" if mw else f"${arg}$" if self.md else f"$${arg}$$")
        if name == "ruby":
            base, _, rest = arg.partition(",")
            r = re.search(r"ruby\s*=\s*([^,]+)", rest)
            rt = r.group(1).strip() if r else ""
            return self.put(f"<ruby>{base}<rt>{rt}</rt></ruby>") if mw or self.md else f"{base}({rt})"
        return m.group(0)

    INLINE_RULES = [  # (찾기, MediaWiki, DokuWiki, Markdown)
        (re.compile(r"'''(.+?)'''"), "'''\\1'''", "**\\1**", "**\\1**"),
        (re.compile(r"''(.+?)''"), "''\\1''", "//\\1//", "*\\1*"),
        (re.compile(r"__(.+?)__"), "<u>\\1</u>", "__\\1__", "<u>\\1</u>"),
        (re.compile(r"~~(.+?)~~"), "<s>\\1</s>", "<del>\\1</del>", "~~\\1~~"),
        (re.compile(r"(?<!-)--(?!-)(.+?)(?<!-)--(?!-)"), "<s>\\1</s>", "<del>\\1</del>", "~~\\1~~"),
        (re.compile(r"\^\^(.+?)\^\^"), "<sup>\\1</sup>", "<sup>\\1</sup>", "<sup>\\1</sup>"),
        (re.compile(r",,(.+?),,"), "<sub>\\1</sub>", "<sub>\\1</sub>", "<sub>\\1</sub>"),
    ]
    MACRO_RE = re.compile(r"\[([a-zA-Z가-힣]+)(\((.*?)\))?\]")
    LINK_RE = re.compile(r"\[\[(.+?)\]\]")
    MATH_RE = re.compile(r"<math>(.*?)</math>", re.S)

    def inline(self, s):
        s = self.MATH_RE.sub(lambda m: self.put(m.group(0) if self.mw else f"${m.group(1)}$" if self.md
                                                 else f"$${m.group(1)}$$"), s)
        s = self.LINK_RE.sub(self.link, s)
        s = self.MACRO_RE.sub(self.macro, s)
        for rx, mw, dk, md in self.INLINE_RULES:
            s = rx.sub(mw if self.mw else md if self.md else dk, s)
        if self.doku:  # DokuWiki 에서 표·문법 기호로 읽힐 글자
            s = s.replace("|", "%%|%%").replace("^", "%%^%%") if "|" in s or "^" in s else s
        return s

    # ---------------- 표
    def table(self, lines):
        caption, rows, tstyle = "", [], {}
        if lines and not lines[0].startswith("||"):
            m = re.match(r"\|([^|]*)\|(.*)", lines[0])
            if m:
                caption, lines[0] = m.group(1), "||" + m.group(2)
        buf = ""
        for ln in lines:
            buf = buf + "\n" + ln if buf else ln
            if buf.rstrip().endswith("||") and len(buf.strip()) > 2:
                rows.append(buf.strip())
                buf = ""
        if buf:
            rows.append(buf.strip() + "||")
        model = []
        for r in rows:
            parts = r[2:-2].split("||")
            cells, span, rowstyle = [], 1, {}
            for p in parts:
                if p == "":
                    span += 1
                    continue
                c = {"colspan": span, "rowspan": 1, "align": "", "bg": "", "color": "", "width": ""}
                span = 1
                while True:
                    m = re.match(r"\s*<([^<>\n]*)>", p)
                    if not m:
                        break
                    a = m.group(1).strip()
                    p = p[m.end():]
                    k, _, v = a.partition("=")
                    k, v = k.strip().lower(), v.strip().strip('"')
                    if re.fullmatch(r"-\d+", a):
                        c["colspan"] = int(a[1:])
                    elif re.fullmatch(r"[\^v]?\|\d+", a):
                        c["rowspan"] = int(a.split("|")[1])
                    elif a in (":", "(", ")"):
                        c["align"] = {":": "center", "(": "left", ")": "right"}[a]
                    elif k in ("bgcolor", "colbgcolor") or re.fullmatch(r"#[0-9a-zA-Z]+(,#?[0-9a-zA-Z]+)?", a):
                        c["bg"] = css_color(v or a)
                    elif k in ("color", "colcolor"):
                        c["color"] = css_color(v)
                    elif k == "width":
                        c["width"] = v
                    elif k == "rowbgcolor":
                        rowstyle["bg"] = css_color(v)
                    elif k == "rowcolor":
                        rowstyle["color"] = css_color(v)
                    elif k.startswith("table"):
                        tstyle[k[5:]] = v
                text = p.strip()
                c["text"] = self.inline(text).replace("\n", "<br />" if self.mw else "<br>" if self.md else "\\\\ ")
                cells.append(c)
            model.append((cells, rowstyle))
        if self.md:
            return self.table_md(caption, model)
        return self.table_mw(caption, model, tstyle) if self.mw else self.table_doku(caption, model)

    def table_md(self, caption, model):
        """칸 합치기가 없으면 GitHub 방식 표, 있으면 HTML 표(마크다운 표로는 합친 칸을 나타낼 수 없다)."""
        if not model:
            return ""
        merged = any(c["colspan"] > 1 or c["rowspan"] > 1 for cells, _ in model for c in cells)
        if merged:
            out = ["<table>"] + ([f"<caption>{self.inline(caption)}</caption>"] if caption else [])
            for cells, _ in model:
                tds = []
                for c in cells:
                    a = (f' colspan="{c["colspan"]}"' if c["colspan"] > 1 else "") + \
                        (f' rowspan="{c["rowspan"]}"' if c["rowspan"] > 1 else "") + \
                        (f' align="{c["align"]}"' if c["align"] else "")
                    tds.append(f"<td{a}>{md_to_html(self.unph(c['text']))}</td>")
                out.append("<tr>" + "".join(tds) + "</tr>")
            return "\n" + "\n".join(out + ["</table>"]) + "\n"
        width = max(len(cells) for cells, _ in model)
        rows = [[c["text"].replace("|", "\\|") for c in cells] + [""] * (width - len(cells)) for cells, _ in model]
        first = model[0][0]
        aligns = [{"center": ":---:", "right": "---:", "left": ":---"}.get(first[i]["align"], "---")
                  if i < len(first) else "---" for i in range(width)]
        out = ([f"**{self.inline(caption)}**", ""] if caption else []) + \
              ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(aligns) + " |"] + \
              ["| " + " | ".join(r) + " |" for r in rows[1:]]
        return "\n" + "\n".join(out) + "\n"

    def table_mw(self, caption, model, tstyle):
        st = []
        if tstyle.get("align") in ("center", "right"):
            st.append("margin-left:auto" + (";margin-right:auto" if tstyle["align"] == "center" else ""))
        if css_color(tstyle.get("bgcolor", "")):
            st.append("background:" + css_color(tstyle["bgcolor"]))
        if tstyle.get("width"):
            st.append("width:" + html.escape(tstyle["width"]))
        out = ['{| class="wikitable"' + (f' style="{";".join(st)}"' if st else "")]
        if caption:
            out.append("|+ " + self.inline(caption))
        for cells, rs in model:
            rst = ";".join(x for x in (rs.get("bg") and "background:" + rs["bg"],
                                       rs.get("color") and "color:" + rs["color"]) if x)
            out.append("|-" + (f' style="{rst}"' if rst else ""))
            for c in cells:
                attrs = []
                if c["colspan"] > 1:
                    attrs.append(f'colspan="{c["colspan"]}"')
                if c["rowspan"] > 1:
                    attrs.append(f'rowspan="{c["rowspan"]}"')
                style = ";".join(x for x in (c["align"] and "text-align:" + c["align"], c["bg"] and "background:" + c["bg"],
                                             c["color"] and "color:" + c["color"],
                                             c["width"] and "width:" + html.escape(c["width"])) if x)
                if style:
                    attrs.append(f'style="{style}"')
                out.append(f"| {' '.join(attrs)} | {c['text']}")
        out.append("|}")
        return "\n".join(out)

    def table_doku(self, caption, model):
        out = [f"**{self.inline(caption)}**"] if caption else []
        carry = {}  # 열 → (남은 줄 수, 폭)
        for cells, _ in model:
            row, col = [], 0
            pending = list(cells)
            while pending or any(k >= col for k in carry):
                if col in carry:
                    n, w = carry[col]
                    row.append(":::")
                    row.extend([""] * (w - 1))
                    if n - 1:
                        carry[col] = (n - 1, w)
                    else:
                        del carry[col]
                    col += w
                    continue
                if not pending:
                    col += 1
                    if col > 200:
                        break
                    continue
                c = pending.pop(0)
                t = c["text"] or " "
                t = {"center": f"  {t}  ", "right": f"  {t} ", "left": f" {t}  "}.get(c["align"], f" {t} ")
                row.append(t)
                row.extend([""] * (c["colspan"] - 1))
                if c["rowspan"] > 1:
                    carry[col] = (c["rowspan"] - 1, c["colspan"])
                col += c["colspan"]
            out.append("|" + "|".join(row) + "|")
        return "\n".join(out)

    # ---------------- 줄 단위
    LIST_RE = re.compile(r"^(\s+)(\*|1\.|a\.|A\.|i\.|I\.)(?:#\d+)?\s?(.*)$")
    HEAD_RE = re.compile(r"^(={1,6})(#?)\s*(.+?)\s*\2\1\s*$")

    def block(self, text):
        lines = text.split("\n")
        out, i, n = [], 0, len(lines)
        kinds = []
        while i < n:
            ln = lines[i]
            if ln.startswith("||") or (re.match(r"^\|[^|]+\|\|", ln)):
                j = i + 1
                buf = [ln]
                while j < n and (not buf[-1].rstrip().endswith("||") or lines[j].startswith("||")):
                    buf.append(lines[j])
                    j += 1
                out.append(self.table(buf))
                kinds.append("block")
                i = j
                continue
            m = self.HEAD_RE.match(ln)
            if m:
                lvl = len(m.group(1))
                h = self.inline(m.group(3))
                if self.mw:
                    out.append(f"{'=' * lvl} {h} {'=' * lvl}")
                elif self.md:
                    out.append(f"\n{'#' * lvl} {h}\n")
                else:
                    k = max(2, 7 - lvl)
                    out.append(f"{'=' * k} {h} {'=' * k}")
                kinds.append("block")
                i += 1
                continue
            if re.fullmatch(r"-{4,9}", ln.strip()):
                out.append("\n---\n" if self.md else "----")
                kinds.append("block")
                i += 1
                continue
            m = self.LIST_RE.match(ln)
            if m:
                depth = max(1, len(m.group(1)))
                ordered = m.group(2) != "*"
                body = self.inline(m.group(3))
                if self.mw:
                    out.append(("#" if ordered else "*") * depth + " " + body)
                elif self.md:
                    out.append("   " * (depth - 1) + ("1. " if ordered else "- ") + body)
                else:
                    out.append("  " * depth + ("- " if ordered else "* ") + body)
                kinds.append("block")
                i += 1
                continue
            m = re.match(r"^(>+)\s?(.*)$", ln)
            if m:
                body = self.inline(m.group(2))
                if self.mw:
                    out.append(f"<blockquote>{body}</blockquote>")
                else:
                    out.append(m.group(1) + " " + body)
                kinds.append("block")
                i += 1
                continue
            m = re.match(r"^(\s+)(\S.*)$", ln)
            if m and not ln.lstrip().startswith(PH):
                body = self.inline(m.group(2))
                out.append((":" * len(m.group(1)) + " " + body) if self.mw else ("> " + body) if self.md else body)
                kinds.append("text")
                i += 1
                continue
            if not ln.strip():
                out.append("")
                kinds.append("blank")
                i += 1
                continue
            out.append(self.inline(ln))
            kinds.append("text")
            i += 1
        # 나무마크는 줄바꿈 하나가 곧 줄바꿈이다. MediaWiki·DokuWiki 는 이어 붙이므로 표시를 넣는다.
        for k in range(len(out) - 1):
            if kinds[k] == "text" and kinds[k + 1] == "text" and out[k].strip() and PH not in out[k][-3:]:
                out[k] += "<br />" if self.mw else "<br>" if self.md else " \\\\"
        return "\n".join(out)

    def convert(self, text):
        text = CTRL_RE.sub("", text.replace("\r\n", "\n"))
        if re.match(r"\s*#redirect\s+", text, re.I):
            target = re.sub(r"\s*#redirect\s+", "", text, count=1, flags=re.I).split("\n")[0].strip()
            page, anchor = resolve(self.title, target)
            if self.mw:
                t = mw_title("category:" + page[3:] if page.startswith("분류:") else page)
                return f"#REDIRECT [[{t}{'#' + anchor if anchor else ''}]]"
            if self.md:
                return f"이 문서는 [{page}](<{md_name(page)}>) 문서로 넘겨줍니다."
            return f"이 문서는 [[{doku_id(page)}|{page}]] 문서로 넘겨줍니다."
        text = re.sub(r"(?m)^##.*\n?", "", text)                       # 주석
        text = re.sub(r"\\(.)", lambda m: self.put(m.group(1)), text)  # \ 로 이스케이프한 글자
        text = self.braces(text)
        text = self.footnotes(text)
        if self.title.startswith("틀:") and self.mw:  # 틀 인자 @이름@, @이름=기본값@
            text = re.sub(r"@([^@\s=]+)(?:=([^@]*))?@",
                          lambda m: self.put("{{{" + m.group(1) + ("|" + m.group(2) if m.group(2) is not None else "") + "}}}"),
                          text)
        body = self.block(text)
        if self.mw and self.refs and not self.has_refs_macro:
            body += "\n\n<references />"
        if self.md and self.notes:
            body += "\n\n" + "\n".join(f"[^{fid}]: {b}" for fid, b in self.notes)
        if self.cats:
            if self.mw:
                body += "\n" + "\n".join(f"[[Category:{mw_title(c)}]]" for c in dict.fromkeys(self.cats))
            elif self.md:
                body += "\n\n---\n\n분류: " + ", ".join(f"[{c}](<{md_name('분류:' + c)}>)" for c in dict.fromkeys(self.cats))
            else:
                body += "\n\n----\n분류: " + ", ".join(f"[[{doku_id('분류:' + c)}|{c}]]" for c in dict.fromkeys(self.cats))
        if self.md:
            body = re.sub(r"\n{3,}", "\n\n", body).strip("\n")
        return self.unph(body)


def md_to_html(s):
    """HTML 표 안에서는 마크다운이 풀리지 않으므로 칸 안의 흔한 마크다운을 HTML 로 바꾼다."""
    s = re.sub(r"\[([^\]]*)\]\(<([^>]*)>\)", lambda m: f'<a href="{urllib.parse.quote(m.group(2))}">{m.group(1)}</a>', s)
    s = re.sub(r"\[([^\]]*)\]\((https?://[^)\s]*)\)", r'<a href="\2">\1</a>', s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<![*\w])\*(.+?)\*(?![*\w])", r"<i>\1</i>", s)
    s = re.sub(r"~~(.+?)~~", r"<s>\1</s>", s)
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", s)


def convert(text, title, target):
    return Conv(target, title).convert(text)


# ---------------------------------------------------------------- 내보내기
PERIOD = ("", "")  # (시작일, 끝일): 주면 그 기간에 바뀐 문서만, 본문은 기간 안의 마지막 판


def _period_args():
    import wiki_pack
    return wiki_pack.period_args(*PERIOD)


def _raw_pages(wiki_dir, limit=0):
    db = sqlite3.connect(f"file:{os.path.join(wiki_dir, 'data.db')}?mode=ro", uri=True, timeout=60)
    lim = f" limit {int(limit)}" if limit else ""
    if any(PERIOD):
        rows = db.execute("select h.title, h.data from history h join (select title, max(id + 0) r from history "
                          "where date between ? and ? group by title) p on p.title = h.title and h.id + 0 = p.r "
                          "order by h.title" + lim, _period_args())
    else:
        rows = db.execute("select title, data from data order by title" + lim)
    for title, data in rows:
        if title.startswith(SKIP_PREFIX) or data is None:
            continue
        yield title, data
    db.close()


TARGET = ""


def _work(item):
    title, data = item
    shown = "분류:" + title[9:] if title.startswith("category:") else title
    try:
        return title, data, convert(data, shown, TARGET), None
    except Exception as e:  # 한 문서가 이상해도 전체를 멈추지 않는다
        return title, data, None, str(e)


def _init(target):
    global TARGET
    TARGET = target


def pages(wiki_dir, target, limit=0, jobs=1):
    """(제목, 원문, 변환 결과 또는 None, 오류) 를 제목 순서대로. jobs 개 CPU 로 나눠 변환한다."""
    if jobs <= 1:
        _init(target)
        yield from map(_work, _raw_pages(wiki_dir, limit))
        return
    import multiprocessing
    with multiprocessing.Pool(jobs, initializer=_init, initargs=(target,)) as pool:
        yield from pool.imap(_work, _raw_pages(wiki_dir, limit), chunksize=64)


def count_pages(wiki_dir, limit=0):
    db = sqlite3.connect(f"file:{os.path.join(wiki_dir, 'data.db')}?mode=ro", uri=True, timeout=60)
    if any(PERIOD):
        n = db.execute("select count(distinct title) from history where date between ? and ?", _period_args()).fetchone()[0]
    else:
        n = db.execute("select count(*) from data").fetchone()[0]
    db.close()
    return min(n, limit) if limit else n


class Progress:
    """진행률과 남은 시간(지금까지의 속도로 추정)을 알린다. 관리판이 이 줄을 읽어 보여 준다."""

    def __init__(self, total):
        self.total, self.t0 = max(total, 1), time.time()
        self.last = self.t0  # 처음 10초는 작업자가 준비하는 동안이라 속도를 재지 않는다

    def tick(self, n, force=False):
        now = time.time()
        if not force and now - self.last < 10:
            return
        self.last = now
        rate = n / max(now - self.t0, 1e-6)
        left = (self.total - n) / rate if rate else 0
        print(f"진행 {n:,}/{self.total:,} ({n * 100 // self.total}%) · 초당 {rate:,.0f}개 · 남은 시간 약 {fmt_secs(left)}",
              flush=True)


def fmt_secs(sec):
    sec = int(sec)
    if sec < 60:
        return f"{sec}초"
    if sec < 3600:
        return f"{sec // 60}분"
    return f"{sec // 3600}시간 {sec % 3600 // 60}분"


def export_mediawiki(wiki_dir, out, limit=0, jobs=1):
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    esc = lambda s: html.escape(s, quote=False)  # noqa: E731
    n, prog = 0, Progress(count_pages(wiki_dir, limit))
    with gzip.open(out + ".part", "wt", encoding="utf-8", compresslevel=6) as f:
        f.write('<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.11/" version="0.11" xml:lang="ko">\n'
                "<siteinfo><sitename>유어위키</sitename><case>first-letter</case></siteinfo>\n")
        f.write("<!-- " + NOTICE.replace("--", "-").replace("\n", " ") + " -->\n")
        seen = set()
        for title, data, text, err in pages(wiki_dir, "mediawiki", limit, jobs):
            t = mw_title(title)
            if t in seen:
                continue
            seen.add(t)
            ns = 14 if t.startswith("Category:") else 10 if t.startswith("Template:") else 0
            if err:
                print(f"  변환 실패(원문 그대로 넣음): {title} — {err}", flush=True)
                text = data
            f.write(f"<page><title>{esc(t)}</title><ns>{ns}</ns><revision><timestamp>{ts}</timestamp>"
                    "<contributor><username>유어위키 변환기</username></contributor>"
                    "<comment>유어위키에서 내보냄 · 나무위키 기여자 · CC BY-NC-SA 2.0 KR</comment>"
                    "<model>wikitext</model><format>text/x-wiki</format>"
                    f'<text xml:space="preserve">{esc(CTRL_RE.sub("", text))}</text></revision></page>\n')
            n += 1
            prog.tick(n)
        f.write("</mediawiki>\n")
    os.replace(out + ".part", out)
    with open(re.sub(r"(\.xml)?(\.gz)?$", "", out) + "-라이선스.txt", "w", encoding="utf-8") as f:
        f.write(NOTICE + "\n가져오기: php maintenance/run.php importDump --report " + os.path.basename(out) + "\n"
                "그다음: php maintenance/run.php rebuildrecentchanges, php maintenance/run.php initSiteStats --update\n"
                "틀 문서는 MediaWiki 의 틀 문법과 다른 부분이 있어 일부는 손으로 고쳐야 할 수 있습니다.\n")
    return n


def export_dokuwiki(wiki_dir, out, limit=0, jobs=1):
    n, prog = 0, Progress(count_pages(wiki_dir, limit))
    seen = set()
    with zipfile.ZipFile(out + ".part", "w", zipfile.ZIP_DEFLATED, allowZip64=True) as z:
        z.writestr("라이선스.txt", NOTICE + "\n이 압축 파일의 data/ 폴더를 DokuWiki 폴더에 덮어 풀고, "
                   "php bin/indexer.php 로 검색 색인을 만드세요.\n파일 이름은 DokuWiki 기본 설정(fnencode = url)에 맞췄습니다.\n")
        for title, data, text, err in pages(wiki_dir, "dokuwiki", limit, jobs):
            pid = base = doku_id(title)
            k = 2
            while pid in seen:  # 대소문자·기호만 다른 제목은 DokuWiki 에서 같은 ID 가 되므로 번호를 붙인다
                pid, k = f"{base}_{k}", k + 1
            seen.add(pid)
            if err:
                print(f"  변환 실패(원문 그대로 넣음): {title} — {err}", flush=True)
                text = data
            z.writestr(doku_path(pid), f"====== {title.replace('category:', '분류:')} ======\n\n" + text + "\n")
            n += 1
            prog.tick(n)
    os.replace(out + ".part", out)
    return n


def export_markdown(wiki_dir, out, limit=0, jobs=1):
    """문서마다 .md 하나. 모두 한 폴더에 두어 [링크](<다른 문서.md>) 가 그대로 이어진다."""
    n, prog, seen = 0, Progress(count_pages(wiki_dir, limit)), set()
    folder = "yourwiki-markdown/"
    with zipfile.ZipFile(out + ".part", "w", zipfile.ZIP_DEFLATED, allowZip64=True) as z:
        z.writestr(folder + "_라이선스.md", "# 라이선스\n\n" + NOTICE.replace("\n", "  \n") +
                   "\n문서마다 .md 파일 하나이고, 문서 사이 링크는 같은 폴더의 파일 이름으로 이어집니다.\n"
                   "Obsidian 같은 마크다운 편집기에서 이 폴더를 열면 됩니다.\n")
        for title, data, text, err in pages(wiki_dir, "markdown", limit, jobs):
            name = base = md_name(title)
            k = 2
            while name.lower() in seen:  # 대소문자만 다른 제목(윈도는 같은 파일로 본다)
                name, k = f"{base[:-3]} ({k}).md", k + 1
            seen.add(name.lower())
            shown = "분류:" + title[9:] if title.startswith("category:") else title
            if err:
                print(f"  변환 실패(원문 그대로 넣음): {title} — {err}", flush=True)
                text = "```\n" + data + "\n```"
            z.writestr(folder + name, f"# {shown}\n\n{text}\n")
            n += 1
            prog.tick(n)
    os.replace(out + ".part", out)
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wiki_dir")
    ap.add_argument("--to", choices=["mediawiki", "dokuwiki", "markdown"], required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--limit", type=int, default=0, help="시험용: N개만")
    ap.add_argument("--jobs", type=int, default=max(1, min(8, (os.cpu_count() or 2) - 1)),
                    help="변환에 쓸 CPU 수(기본: 전체 - 1, 최대 8)")
    ap.add_argument("--since", dest="frm", default="", help="이 날(YYYY-MM-DD)부터 바뀐 문서만")
    ap.add_argument("--until", dest="until", default="", help="이 날(YYYY-MM-DD)까지 바뀐 문서만")
    args = ap.parse_args()
    global PERIOD
    for d in (args.frm, args.until):
        if d and not re.match(r"\d{4}-\d{2}-\d{2}$", d):
            raise SystemExit(f"날짜는 YYYY-MM-DD 로: {d}")
    PERIOD = (args.frm, args.until)
    names = {"mediawiki": "yourwiki-mediawiki.xml.gz", "dokuwiki": "yourwiki-dokuwiki.zip",
             "markdown": "yourwiki-markdown.zip"}
    out = args.out or os.path.join(os.path.dirname(os.path.abspath(args.wiki_dir)), "export", names[args.to])
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    span = f"{args.frm or '처음'}~{args.until or time.strftime('%Y-%m-%d')}" if any(PERIOD) else ""
    if span and not args.out:
        out = re.sub(r"(\.xml\.gz|\.zip)$", f"-{span}\\1", out)
    print(f"{args.to} 로 내보내기 시작 → {out} (CPU {args.jobs}개" + (f", 기간 {span}" if span else "") + ")", flush=True)
    print(NOTICE, flush=True)
    t0 = time.time()
    n = {"mediawiki": export_mediawiki, "dokuwiki": export_dokuwiki,
         "markdown": export_markdown}[args.to](args.wiki_dir, out, args.limit, args.jobs)
    print(f"완료: 문서 {n:,}개, {time.time() - t0:.0f}초 → {out}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
