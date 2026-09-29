"""direct.py 시험: 가짜 공유기(UPnP)로 포트 빌리기·충돌·영구만 되는 공유기·CGNAT 거절, 그리고 남의 주소 규칙(public_url_ok)."""
import http.server
import os
import re
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import direct  # noqa: E402
import p2p  # noqa: E402

DESC = """<?xml version="1.0"?><root xmlns="urn:schemas-upnp-org:device-1-0"><device><deviceList><device>
<serviceList><service><serviceType>urn:schemas-upnp-org:service:WANIPConnection:1</serviceType>
<controlURL>/ctl/IPConn</controlURL></service></serviceList></device></deviceList></device></root>"""


def fake_router(ext, conflict=(), perm_only=False):
    st = {"maps": {}}

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self._send(200, DESC.encode())

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"])).decode()
            act = self.headers["SOAPAction"].split("#")[1].strip('"')

            def f(n):
                m = re.search(f"<{n}>(.*?)</{n}>", body)
                return m.group(1) if m else ""
            err, out = None, ""
            if act == "GetExternalIPAddress":
                out = f"<NewExternalIPAddress>{ext}</NewExternalIPAddress>"
            elif act == "AddPortMapping":
                port = int(f("NewExternalPort"))
                if port in conflict:
                    err = 718
                elif perm_only and f("NewLeaseDuration") != "0":
                    err = 725
                else:
                    st["maps"][port] = f("NewLeaseDuration")
            elif act == "DeletePortMapping":
                st["maps"].pop(int(f("NewExternalPort")), None)
            if err:
                self._send(500, f"<Envelope><Body><Fault><detail><UPnPError><errorCode>{err}</errorCode>"
                                f"</UPnPError></detail></Fault></Body></Envelope>".encode())
            else:
                self._send(200, f'<s:Envelope xmlns:s="x"><s:Body><u:R xmlns:u="y">{out}</u:R></s:Body></s:Envelope>'.encode())

        def _send(self, code, b):
            self.send_response(code)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    os.environ["YOURWIKI_UPNP_LOCATION"] = f"http://127.0.0.1:{srv.server_address[1]}/desc.xml"
    return srv, st


def run(ext, **kw):
    srv, st = fake_router(ext, **kw)
    direct.public_ipv4 = lambda: ""  # 이 시험 기계의 실제 주소와 상관없이
    direct.ipv6_address = lambda: ""
    d = direct.Direct(3002, "00000fff" + "0" * 56, log=lambda m: None)
    urls, maps = d.refresh(), dict(st["maps"])
    d.close()
    srv.shutdown()
    return urls, maps, st["maps"]


def main():
    base = direct.port_for("00000fff" + "0" * 56)
    urls, maps, after = run("8.8.4.4")
    assert urls == [f"http://8.8.4.4:{base}"] and maps == {base: "3600"} and after == {}, (urls, maps, after)
    urls, maps, _ = run("8.8.4.4", conflict={base})
    assert urls == [f"http://8.8.4.4:{base + 7}"], urls
    urls, maps, _ = run("8.8.4.4", perm_only=True)
    assert maps == {base: "0"}, maps
    urls, maps, _ = run("100.64.1.2")  # 통신사 공유 IP(CGNAT)
    assert urls == [] and maps == {}, (urls, maps)
    os.environ.pop("YOURWIKI_P2P_ALLOW_LOCAL", None)
    rules = {"http://8.8.8.8:24095": True, "http://8.8.8.8:80": False, "http://192.168.0.2:3002": False,
             "http://[2001:4860::1]:3002": True, "http://[fe80::1]:3002": False, "http://example.com:3002": False,
             "https://example.com": True, "ftp://8.8.8.8:3000": False}
    for u, want in rules.items():
        assert p2p.public_url_ok(u, resolve=False) == want, u
    print("test_direct: 통과")


if __name__ == "__main__":
    main()
