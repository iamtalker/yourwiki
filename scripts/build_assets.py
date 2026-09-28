"""(개발용) openNAMU 가 CDN 에서 불러오는 파일과 아이콘을 받아 assets/ 에 동봉한다.

openNAMU 버전을 올리면 CDN 주소가 바뀔 수 있으니, 그때 이 목록을 갱신하고 다시 실행한다.
- assets/cdn/<호스트>/<경로>  : offline_proxy.py 가 /_kit/cdn/... 로 제공
- assets/icons.json           : Material Icons(Apache-2.0) 중 openNAMU 가 쓰는 것만
"""
import json
import os
import re
import urllib.request

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets")

CDN_FILES = [
    "https://cdn.jsdelivr.net/npm/katex@0.16.38/dist/katex.min.js",
    "https://cdn.jsdelivr.net/npm/katex@0.16.38/dist/katex.min.css",
    "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.8.0/highlight.min.js",
    "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.8.0/languages/x86asm.min.js",
    "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.8.0/styles/default.min.css",
    "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.8.0/styles/dark.min.css",
    "https://cdnjs.cloudflare.com/ajax/libs/highlightjs-line-numbers.js/2.8.0/highlightjs-line-numbers.min.js",
    "https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.56.0/min/vs/editor/editor.main.min.css",
]

ICONS = """baseline-access-time baseline-account-box baseline-add-alert baseline-add-comment
baseline-archive baseline-arrow-downward baseline-arrow-drop-down baseline-arrow-upward
baseline-autorenew baseline-build baseline-cloud-upload baseline-contact-mail baseline-how-to-reg
baseline-how-to-vote baseline-list baseline-login baseline-logout baseline-manage-accounts
baseline-person-add baseline-person-add-alt-1 baseline-playlist-add baseline-plus baseline-search
baseline-settings baseline-shuffle outline-developer-board round-find-in-page round-person-search
round-preview twotone-stars""".split()


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "namu-fork-kit build_assets"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def save(url, data):
    path = os.path.join(ROOT, "cdn", *url.split("://", 1)[1].split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def main():
    for url in CDN_FILES:
        data = fetch(url)
        save(url, data)
        if url.endswith("katex.min.css"):  # 수식 글꼴은 css 기준 상대 경로(fonts/...)로 불러온다
            base = url.rsplit("/", 1)[0] + "/"
            for font in sorted(set(re.findall(r"url\((fonts/[^)]+\.woff2)\)", data.decode()))):
                save(base + font, fetch(base + font))
    icons = json.loads(fetch("https://api.iconify.design/ic.json?icons=" + ",".join(ICONS)))
    with open(os.path.join(ROOT, "icons.json"), "w", encoding="utf-8") as f:
        json.dump(icons, f, ensure_ascii=False)
    print("완료:", len(CDN_FILES), "개 CDN 파일,", len(icons.get("icons", {})), "개 아이콘")


if __name__ == "__main__":
    main()
