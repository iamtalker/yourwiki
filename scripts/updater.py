"""갱신기: 나무위키에서 바뀐 문서를 예의 바르게 받아 위키를 최신으로 유지한다 (유어위키 1.1).

원칙 (시작할 때 로그 첫 줄에 남긴다)
- robots.txt 를 지킨다. 시작할 때와 1시간마다 다시 읽고, 필요한 경로가 금지되면 스스로 멈춘다.
- 요청은 6초에 1건(분당 10건) 이하. User-Agent 에 프로젝트 주소를 밝힌다.
- 캡차·차단·이상 응답을 감지하면 즉시 멈춘다. 우회하지 않는다.
- 같은 문서는 대기열에서 하나로 합치고, 한 번 받은 문서는 24시간 동안 다시 받지 않는다.

대기열은 <wiki>/updater.db 에 있다. 중계 서버의 '최신판으로 갱신' 단추도 여기에 문서를 넣는다.

사용:
  python updater.py <wiki 폴더> --doc 문서명        # 문서 하나만 갱신
  python updater.py <wiki 폴더> --watch             # 최근 변경을 따라가며 계속 갱신
"""
import argparse
import html as htmlmod
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import html2namu  # noqa: E402

BASE = "https://namu.wiki"
UA = "YourWiki/1.0 (+https://github.com/iamtalker/yourwiki)"
INTERVAL = 6.0            # 초 (분당 10건)
COOLDOWN = 24 * 3600      # 같은 문서 다시 받기까지
RC_EVERY = 300            # 최근 변경 확인 주기(초)
ROBOTS_EVERY = 3600
SKIP_PREFIX = ("틀:", "파일:", "사용자:", "특수기능:", "휴지통:", "파일휴지통:")
PRINCIPLE = ("[원칙] robots.txt 준수 · 6초에 1건 이하 · 캡차/차단/이상 응답 감지 시 즉시 중단 · 우회하지 않음 · "
             f"User-Agent: {UA}")
LICENSE_URL = "https://creativecommons.org/licenses/by-nc-sa/2.0/kr/"


class Stop(Exception):
    """멈춰야 하는 상황(차단, robots 금지 등)."""


def log(msg):
    print(time.strftime("[%Y-%m-%d %H:%M:%S] ") + msg, flush=True)


class Fetcher:
    def __init__(self):
        self.last = 0.0
        self.robots = None
        self.robots_at = 0.0

    @staticmethod
    def allowed(robots_txt, path):
        """RFC 9309 방식: User-agent: * 묶음에서 가장 길게 맞는 규칙을 따른다(같으면 Allow).

        파이썬 urllib.robotparser 는 위에서부터 처음 맞는 규칙을 써서,
        'Disallow: /' 다음에 'Allow: /w/' 를 둔 나무위키 robots.txt 를 잘못 해석한다.
        """
        rules, active, in_group = [], False, False
        for line in robots_txt.splitlines():
            line = line.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            k, v = (x.strip() for x in line.split(":", 1))
            k = k.lower()
            if k == "user-agent":
                if not in_group:
                    active = False
                in_group = False
                active = active or v == "*"
            elif k in ("allow", "disallow"):
                in_group = True
                if active and v:
                    rules.append((k == "allow", v))
        best = (-1, True)
        for allow, pat in rules:
            rx = "^" + re.escape(pat).replace(r"\*", ".*").replace(r"\$", "$")
            if re.match(rx, path) and (len(pat) > best[0] or (len(pat) == best[0] and allow)):
                best = (len(pat), allow)
        return best[1]

    def check_robots(self):
        if self.robots and time.time() - self.robots_at < ROBOTS_EVERY:
            return
        req = urllib.request.Request(BASE + "/robots.txt", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            txt = r.read().decode("utf-8", "replace")
        for path in ("/w/%EB%82%98%EB%AC%B4", "/RecentChanges"):
            if not self.allowed(txt, path):
                raise Stop(f"robots.txt 가 {path} 를 금지함 → 중단")
        self.robots, self.robots_at = txt, time.time()

    def get(self, path):
        self.check_robots()
        wait = INTERVAL - (time.time() - self.last)
        if wait > 0:
            time.sleep(wait)
        self.last = time.time()
        req = urllib.request.Request(BASE + path, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise Stop(f"HTTP {e.code} ({path}) → 차단 가능성, 중단")
        low = body[:20000].lower()
        if any(k in low for k in ("captcha-challenge", "cf-chl", "just a moment", "g-recaptcha\"", "hcaptcha-box")):
            raise Stop(f"캡차/봇 확인 화면 감지 ({path}) → 중단")
        return body


# ---------------------------------------------------------------- 대기열
def open_queue(wiki_dir):
    q = sqlite3.connect(os.path.join(wiki_dir, "updater.db"), timeout=30)
    q.executescript("""
        create table if not exists queue (title text primary key, priority int, reason text, added real);
        create table if not exists fetched (title text primary key, at real, namu_modified text);
    """)
    return q


def enqueue(q, title, priority=0, reason=""):
    if not title or title.startswith(SKIP_PREFIX):
        return
    q.execute("insert into queue values (?, ?, ?, ?) on conflict(title) do update set "
              "priority = max(priority, excluded.priority)", (title, priority, reason, time.time()))


def next_title(q):
    now = time.time()
    for title, in q.execute("select title from queue order by priority desc, added asc").fetchall():
        row = q.execute("select at from fetched where title = ?", (title,)).fetchone()
        if row and now - row[0] < COOLDOWN:
            q.execute("delete from queue where title = ?", (title,))  # 쿨다운 중이면 버린다
            continue
        return title
    return None


# ---------------------------------------------------------------- 위키 반영
def wiki_title(t):
    return "category:" + t[3:] if t.startswith("분류:") else t


MODIFIED_RE = re.compile(r"최근 수정 시각\s*:?\s*(?:<[^>]+>\s*)*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
CAT_RE = re.compile(r"\[\[분류:([^\]|#\n]+)")


def footer(title, modified):
    q = urllib.parse.quote(title, safe="")
    return ("\n\n----\n"
            f" * 출처: [[{BASE}/w/{q}|나무위키 「{title}」 문서]] (최근 수정 {modified}, 유어위키 갱신기로 가져옴)\n"
            f" * 라이선스: [[{LICENSE_URL}|CC BY-NC-SA 2.0 KR]] · 저작권은 각 기여자에게 있습니다. "
            f"기여자 목록은 [[{BASE}/history/{q}|원 문서의 역사]]에서 볼 수 있습니다.\n")


def apply(wiki_dir, title, text, info, modified):
    """문서를 새 판으로 올린다. 바뀐 게 없으면 False."""
    db = sqlite3.connect(os.path.join(wiki_dir, "data.db"), timeout=60)
    wt = wiki_title(title)
    last = db.execute("select set_data from data_set where doc_name = ? and set_name = 'last_edit'",
                      (wt,)).fetchone()
    if last and modified and last[0][:19] >= modified:
        db.close()
        return False
    data = text if info.get("redirect") else text.rstrip("\n") + footer(title, modified)
    exists = db.execute("select 1 from data where title = ?", (wt,)).fetchone()
    if exists:
        db.execute("update data set data = ? where title = ?", (data, wt))
    else:
        db.execute("insert into data (title, data, type) values (?, ?, '')", (wt, data))
    rev = (db.execute("select max(id + 0) from history where title = ?", (wt,)).fetchone()[0] or 0) + 1
    db.execute("insert into history (id, title, data, date, ip, send, leng, hide, type) "
               "values (?, ?, ?, ?, '유어위키 갱신기', ?, ?, '', ?)",
               (str(rev), wt, data, time.strftime("%Y-%m-%d %H:%M:%S"),
                f"나무위키 최신판(수정 {modified})에서 갱신", str(len(data)), "r1" if rev == 1 else ""))
    db.execute("delete from data_set where doc_name = ? and set_name in ('last_edit', 'length')", (wt,))
    db.executemany("insert into data_set (doc_name, doc_rev, set_name, set_data) values (?, '', ?, ?)",
                   [(wt, "last_edit", modified or time.strftime("%Y-%m-%d %H:%M:%S")), (wt, "length", str(len(data)))])
    db.execute("delete from back where link = ? and type in ('cat', 'redirect')", (wt,))
    if info.get("redirect"):
        db.execute("insert into back (link, title, type, data) values (?, ?, 'redirect', '')",
                   (wt, wiki_title(info["redirect"])))
    else:
        db.executemany("insert into back (link, title, type, data) values (?, ?, 'cat', '')",
                       [(wt, "category:" + c.strip()) for c in set(CAT_RE.findall(text))])
    db.commit()
    db.close()
    return True


def refresh(f, q, wiki_dir, size_map, title):
    body = f.get("/w/" + urllib.parse.quote(title, safe=""))
    q.execute("delete from queue where title = ?", (title,))
    if body is None:
        log(f"없음(404): {title}")
        q.execute("insert or replace into fetched values (?, ?, ?)", (title, time.time(), ""))
        q.commit()
        return
    if "이 저작물은" not in body:
        raise Stop(f"문서 화면이 예상과 다름({title}) → 차단 화면일 수 있어 중단")
    m = MODIFIED_RE.search(re.sub(r"<!--.*?-->", "", body))
    modified = m.group(1) if m else ""
    text, info = html2namu.convert(body, size_map, title)
    changed = apply(wiki_dir, title, text, info, modified)
    q.execute("insert or replace into fetched values (?, ?, ?)", (title, time.time(), modified))
    q.commit()
    log(f"{'갱신' if changed else '변경 없음'}: {title} (나무위키 수정 {modified or '?'})")


RC_LINK_RE = re.compile(r'href="/w/([^"#?]+)"')


def poll_recent(f, q):
    body = f.get("/RecentChanges")
    titles = []
    for m in RC_LINK_RE.finditer(body or ""):
        t = urllib.parse.unquote(htmlmod.unescape(m.group(1)))
        if t not in titles:
            titles.append(t)
    for t in titles:
        enqueue(q, t, 1, "최근 변경")
    q.commit()
    log(f"최근 변경 확인: {len(titles)}개 대기열에 넣음")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wiki_dir")
    ap.add_argument("--doc")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--queue-only", action="store_true",
                    help="최근 변경은 따라가지 않고 대기열(갱신 단추로 요청한 문서)만 처리")
    ap.add_argument("--classmap", default=os.path.join(os.path.dirname(__file__), "..", "assets", "classmap.json"))
    args = ap.parse_args()

    size_map = json.load(open(args.classmap, encoding="utf-8")).get("size", {})
    log(PRINCIPLE)
    f, q = Fetcher(), open_queue(args.wiki_dir)
    try:
        if args.doc:
            refresh(f, q, args.wiki_dir, size_map, args.doc)
            return
        if not args.watch:
            ap.error("--doc 또는 --watch 가 필요합니다")
        last_rc = 0.0
        while True:
            if not args.queue_only and time.time() - last_rc > RC_EVERY:
                poll_recent(f, q)
                last_rc = time.time()
            title = next_title(q)
            if title:
                refresh(f, q, args.wiki_dir, size_map, title)
            else:
                time.sleep(10)
    except Stop as e:
        log(f"중단: {e}")
        sys.exit(2)
    except KeyboardInterrupt:
        log("사용자가 멈춤")


if __name__ == "__main__":
    main()
