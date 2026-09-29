"""새 판 알림: GitHub 릴리스를 가끔 확인해 새 유어위키 판이 나왔는지 알려 준다 (유어위키 1.2, 표준 라이브러리만 사용).

- 알리기만 하고 스스로 설치하지 않는다. 받을지는 사람이 릴리스 내용을 보고 정한다.
- 확인하는 곳은 sources.json 의 update.repo (기본 iamtalker/yourwiki) 의 '최신 릴리스'뿐이다.
  보내는 것은 없고(내 ID·문서 등), GitHub 가 안 되면 조용히 넘어간다. 위키·P2P 동작과는 상관없다.
- 결과는 update.json 에 기억해 두고, 12시간에 한 번만 GitHub 에 묻는다.

    python update_check.py            # 지금 확인해서 한 줄로 알려 줌
    python update_check.py --quiet    # 새 판이 있을 때만 출력(리눅스 시작 스크립트용)
"""
import json
import os
import re
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "update.json")
UA = "YourWiki-update-check (+https://github.com/iamtalker/yourwiki)"


def config():
    try:
        c = json.load(open(os.path.join(ROOT, "sources.json"), encoding="utf-8")).get("update", {})
    except (OSError, ValueError):
        c = {}
    return c.get("repo", "iamtalker/yourwiki"), float(c.get("every_hours", 12))


def current_version():
    try:
        m = re.search(r'KIT_VERSION = "([^"]+)"', open(os.path.join(ROOT, "scripts", "panel.py"), encoding="utf-8").read())
        return m.group(1) if m else "0"
    except OSError:
        return "0"


def vtuple(v):
    """'v1.2.1' → (1, 2, 1). 숫자가 아닌 꼬리(-beta 등)는 버린다."""
    return tuple(int(x) for x in re.findall(r"\d+", (v or "").split("-")[0])[:4]) or (0,)


def fetch(repo):
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases/latest",
                                 headers={"User-Agent": UA, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        d = json.loads(r.read(1 << 20).decode("utf-8"))
    return {"latest": d.get("tag_name", ""), "name": d.get("name") or d.get("tag_name", ""),
            "url": d.get("html_url", f"https://github.com/{repo}/releases"),
            "published": (d.get("published_at") or "")[:10], "notes": (d.get("body") or "")[:1500]}


def check(force=False, current=None):
    """{'current', 'latest', 'newer', 'url', 'name', 'notes', 'checked_at', 'error'}."""
    repo, every = config()
    current = current or current_version()
    try:
        cache = json.load(open(CACHE, encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    if force or time.time() - cache.get("checked_at", 0) > every * 3600 or cache.get("repo") != repo:
        try:
            cache = dict(fetch(repo), repo=repo, checked_at=time.time(), error="")
        except Exception as e:  # 인터넷이 없거나 GitHub 가 안 될 때: 전에 알던 것을 그대로 쓰고 조용히 넘어간다
            cache = dict(cache, repo=repo, checked_at=time.time(), error=str(e)[:200])
        try:
            with open(CACHE, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False)
        except OSError:
            pass
    latest = cache.get("latest", "")
    return dict(cache, current=current, newer=bool(latest) and vtuple(latest) > vtuple(current))


def message(r):
    if r.get("newer"):
        return (f"새 판이 나왔습니다: {r.get('name') or r['latest']} ({r.get('published', '')}) · 지금 {r['current']} · "
                f"{r.get('url')}")
    if r.get("latest"):
        return f"최신 판입니다 ({r['current']}, GitHub 최신 {r['latest']})"
    return f"새 판을 확인하지 못했습니다: {r.get('error') or '알 수 없음'}"


if __name__ == "__main__":
    quiet = "--quiet" in sys.argv
    r = check(force=not quiet)
    if r.get("newer") or not quiet:
        print(message(r))
