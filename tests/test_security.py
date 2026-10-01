"""P2P 보안 시험: 남의 창구가 보낸 값을 믿지 않는지.
- 리다이렉트를 따라가지 않는다(내부망으로 요청을 돌리는 데 못 쓰게)
- 제목·넘겨주기 대상의 길이·제어 문자 검사, 묶음 크기 제한"""
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import hub  # noqa: E402
import p2p  # noqa: E402

fails = 0
hits = []


def check(cond, msg):
    global fails
    if not cond:
        fails += 1
        print("실패:", msg)


class Target(BaseHTTPRequestHandler):
    """리다이렉트로 몰려 올 '내부' 서버. 여기에 요청이 오면 안 된다."""
    def log_message(self, *a):
        pass

    def do_GET(self):
        hits.append(self.path)
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_POST = do_GET


class Evil(BaseHTTPRequestHandler):
    """창구인 척하며 다른 주소로 가라고 하는 서버."""
    def log_message(self, *a):
        pass

    def _go(self):
        self.send_response(302)
        self.send_header("Location", f"http://127.0.0.1:{inner.server_address[1]}/internal")
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_GET = do_POST = _go


inner = ThreadingHTTPServer(("127.0.0.1", 0), Target)
evil = ThreadingHTTPServer(("127.0.0.1", 0), Evil)
for s in (inner, evil):
    threading.Thread(target=s.serve_forever, daemon=True).start()
try:
    for data in (None, b"{}"):
        try:
            p2p.http_json(f"http://127.0.0.1:{evil.server_address[1]}/_hub/changes?since=0", data=data, timeout=5)
            check(False, "리다이렉트하는 서버의 응답을 받아들이면 안 됨")
        except ValueError as e:
            check("302" in str(e), f"HTTP 302 로 실패해야 함: {e}")
    check(not hits, f"리다이렉트를 따라가 내부 서버에 요청이 갔음: {hits}")
finally:
    inner.shutdown()
    evil.shutdown()

for t, ok in (("문서", True), ("분류:가나다", True), ("a" * 512, True), ("a" * 513, False), ("", False),
              ("줄\n바꿈", False), ("탭\t문자", False), ("널" + chr(0), False), ("DEL" + chr(127), False), (None, False), (5, False)):
    check(hub.title_ok(t) == ok, f"제목 검사 {t!r} → {hub.title_ok(t)} (기대 {ok})")

sha = p2p.digest("본문", "")
check(hub.good_doc(["본문", ""], sha), "정상 문서")
bad_redirect = "대상\n문서"
check(not hub.good_doc(["본문", bad_redirect], p2p.digest("본문", bad_redirect)), "제어 문자가 든 넘겨주기 대상")
long_redirect = "가" * 600
check(not hub.good_doc(["본문", long_redirect], p2p.digest("본문", long_redirect)), "너무 긴 넘겨주기 대상")

print("보안 시험 통과" if not fails else f"보안 시험 {fails}개 실패")
sys.exit(1 if fails else 0)
