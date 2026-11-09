#!/usr/bin/env python3
"""새로 만들어진 나무위키 문서 목록을 1분마다 기록한다(휴대폰·Termux 용, 독립 실행).

- /RecentChanges?logtype=create 만 읽는다. 문서 이름공간(틀:·파일:·사용자: 등 제외)만 기록한다.
- 나무위키 요청 규칙: robots.txt 를 지키고, 요청 사이 6초 이상 띄우며, 차단(403 등)이 보이면 즉시 멈춘다.
- 결과는 한 줄씩 쌓이는 텍스트 파일이다: 첫 발견 시각(UTC)<TAB>제목
- 사용: python3 create_watch.py [저장_폴더]   (중지: Ctrl+C)
"""
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE = "https://namu.wiki"
UA = "YourWiki/1.0 (+https://github.com/iamtalker/yourwiki)"
INTERVAL = 6.0        # 요청 사이 최소 간격(초)
POLL_EVERY = 60       # 목록을 다시 읽는 주기(초). 100건이 약 2분 분량이라 60초면 빠지는 것이 거의 없다.
ROBOTS_EVERY = 3600
SKIP_PREFIX = ("틀:", "파일:", "사용자:", "삭제된사용자:", "특수기능:", "휴지통:", "파일휴지통:")
LINK_RE = re.compile(r'href="/w/([^"#?]+)"')


class Stop(Exception):
    """차단 등 멈춰야 하는 상황."""


def log(msg):
    print(datetime.now().strftime("[%Y-%m-%d %H:%M:%S] ") + msg, flush=True)


def allowed(robots_txt, path):
    """User-agent: * 묶음에서 가장 길게 맞는 규칙을 따른다(RFC 9309). 같은 길이면 허용."""
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
    for is_allow, pat in rules:
        rx = "^" + re.escape(pat).replace(r"\*", ".*").replace(r"\$", "$")
        if re.match(rx, path) and (len(pat) > best[0] or (len(pat) == best[0] and is_allow)):
            best = (len(pat), is_allow)
    return best[1]


class Client:
    def __init__(self):
        self.last = 0.0
        self.robots = None
        self.robots_at = 0.0

    def check_robots(self):
        if self.robots and time.time() - self.robots_at < ROBOTS_EVERY:
            return
        req = urllib.request.Request(BASE + "/robots.txt", headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                txt = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            raise Stop(f"robots.txt 를 읽지 못함(HTTP {e.code}) → 중단")
        if not allowed(txt, "/RecentChanges"):
            raise Stop("robots.txt 가 /RecentChanges 를 금지함 → 중단")
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
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 429) or e.code >= 500:
                raise Stop(f"HTTP {e.code} → 차단 가능성, 중단")
            raise
        except (urllib.error.URLError, OSError) as e:
            raise ConnectionError(str(e))


def load_seen(path):
    seen = set()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                parts = line.rstrip("\n").split("\t", 1)
                if len(parts) == 2:
                    seen.add(parts[1])
    return seen


def main():
    folder = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.expanduser("~"), "yourwiki-watch")
    os.makedirs(folder, exist_ok=True)
    out_path = os.path.join(folder, "new_documents.tsv")
    seen = load_seen(out_path)
    log(f"기록 시작: {out_path} (이미 기록된 문서 {len(seen)}개)")
    client = Client()
    fails = 0
    while True:
        try:
            body = client.get("/RecentChanges?logtype=create")
            fails = 0
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            added = []
            for m in LINK_RE.finditer(body):
                title = urllib.parse.unquote(m.group(1))
                if title.startswith(SKIP_PREFIX) or title in seen:
                    continue
                seen.add(title)
                added.append(title)
            if added:
                with open(out_path, "a", encoding="utf-8") as fh:
                    for t in added:
                        fh.write(f"{now}\t{t}\n")
            log(f"목록 확인: 새로 기록 {len(added)}개 (누적 {len(seen)}개)")
        except Stop as e:
            log(f"멈춤: {e}")
            return
        except ConnectionError as e:
            fails += 1
            wait = min(1800, 60 * 2 ** min(fails, 5))
            log(f"연결 실패({e}), {wait}초 뒤 다시 시도")
            time.sleep(wait)
            continue
        time.sleep(POLL_EVERY)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("직접 중지함")
