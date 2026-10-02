"""터널 프로그램(cloudflared)을 공식 배포처(GitHub)에서 받아 SHA-256 으로 검증한다 (Windows·리눅스).

관리판의 [공개하기](위키 공개)와 P2P 전용 창구 공개에 쓴다. 계정 없이 쓰는 '빠른 터널'이라
켤 때마다 https://…trycloudflare.com 임시 주소가 새로 나온다.

    python cloudflared.py        # 받아 두고 경로를 출력
"""
import hashlib
import json
import os
import platform
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def tool_key():
    if os.name == "nt":
        return "cloudflared"
    arch = platform.machine().lower()
    return "cloudflared-linux-arm64" if arch in ("aarch64", "arm64") else "cloudflared-linux-amd64"


def ensure():
    key = tool_key()
    t = json.load(open(os.path.join(ROOT, "sources.json"), encoding="utf-8"))["tools"][key]
    os.makedirs(os.path.join(ROOT, "tools"), exist_ok=True)
    exe = os.path.join(ROOT, "tools", "cloudflared.exe" if os.name == "nt" else "cloudflared")

    def ok():
        if not os.path.exists(exe):
            return False
        h = hashlib.sha256()
        with open(exe, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest() == t["sha256"]
    if not ok():
        urllib.request.urlretrieve(t["url"], exe + ".part")
        os.replace(exe + ".part", exe)
        if not ok():
            os.remove(exe)
            raise RuntimeError("터널 프로그램 해시가 맞지 않습니다")
    if os.name != "nt":
        os.chmod(exe, 0o755)
    return exe


if __name__ == "__main__":
    try:
        print(ensure())
    except Exception as e:
        print(f"터널 프로그램을 받지 못했습니다: {e}", file=sys.stderr)
        sys.exit(1)
