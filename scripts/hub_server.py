"""중계소만 따로 띄우기 (위키 없이, 유어위키 1.2, 표준 라이브러리만 사용).

중계소(hub.py)는 위키 전체(40GB)가 필요 없다. 최근 30일 동안 오간 문서만 보관하므로
작은 무료 서버(Oracle Cloud 무료 등)에서도 충분하다.

    python hub_server.py [--listen 0.0.0.0:8080] [--db hub.db]

문서마다 보낸 사람의 서명과 해시가 붙어 있어 중계소를 거쳐도 내용을 바꿀 수 없다.
그래서 HTTPS 없이 http://공인IP:8080 으로 열어도 내용은 안전하다(누가 무엇을 주고받는지는 보일 수 있다).
"""
import argparse
import http.server
import json
import os
import socketserver
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hub  # noqa: E402

MAX_BODY = 64 << 20


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    db_path = "hub.db"

    def log_message(self, fmt, *args):
        pass

    def _serve(self):
        u = urllib.parse.urlsplit(self.path)
        if u.path == "/":
            return self._send(200, {"app": hub.APP, "안내": "유어위키 중계소입니다. 유어위키 관리판의 '중계소 주소'에 이 주소를 적으세요."})
        if not u.path.startswith("/_hub/"):
            return self._send(404, {"error": "없음"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0:
            return self._send(400, {"error": "Content-Length 가 잘못됨"})
        if length > MAX_BODY:
            return self._send(413, {"error": "너무 큼"})
        body = self.rfile.read(length) if length else b""
        try:
            code, obj = hub.handle(self.db_path, self.command, u.path, urllib.parse.parse_qs(u.query), body)
        except Exception as e:  # 한 요청이 이상해도 서버는 계속 돈다
            code, obj = 500, {"error": str(e)[:200]}
        if u.path == "/_hub/submit":
            print(time.strftime("[%Y-%m-%d %H:%M:%S] ") + f"submit {code} {self.client_address[0]} "
                  f"{obj.get('error', '')}", flush=True)
        self._send(code, obj)

    def _send(self, code, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    do_GET = do_POST = do_HEAD = _serve


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", default="0.0.0.0:8080")
    ap.add_argument("--db", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hub.db"))
    args = ap.parse_args()
    host, port = args.listen.rsplit(":", 1)
    Handler.db_path = args.db
    hub.open_db(args.db).close()
    print(f"유어위키 중계소: http://{host}:{port}  (저장소 {args.db})", flush=True)
    Server((host, int(port)), Handler).serve_forever()


if __name__ == "__main__":
    main()
