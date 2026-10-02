"""두 위키: A 가 DHT 에 주소 두 개(첫째는 닿지 않음)를 올리면, B 가 둘째 주소로 받고 순서를 바꿔 기억하는지."""
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["YOURWIKI_DHT_ALLOW_LOCAL"] = "1"
os.environ["YOURWIKI_P2P_ALLOW_LOCAL"] = "1"
import fakedht  # noqa: E402


def main():
    net = fakedht.start(20)
    os.environ["YOURWIKI_DHT_BOOTSTRAP"] = f"127.0.0.1:{net[0].addr[1]}"
    import p2p
    root = tempfile.mkdtemp(prefix="yw-alt-")
    a, b = os.path.join(root, "A"), os.path.join(root, "B")
    os.makedirs(a)
    os.makedirs(b)
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    win = subprocess.Popen([sys.executable, os.path.join(ROOT, "scripts", "hub_server.py"), "--read-only",
                            "--listen", f"[::]:{port}", "--db", os.path.join(a, "hub.db")],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(1)
        wa = p2p.Worker(a, (), (), self_url=f"http://127.0.0.1:{port}")

        class Blocked:  # 공유기 방화벽에 막힌 직접 주소 흉내
            def refresh(self):
                return ["http://127.0.0.1:9"]

            def close(self):
                pass
        wa.direct = Blocked()
        p2p.record(wa.q, "시험 문서", "내용 " * 50, "", "2026-09-01 00:00:00", "namu")
        wa.q.commit()
        wa.round()
        wb = p2p.Worker(b, (), [wa.me])
        wb.wanted = lambda limit=300: []  # 본문 받기(data.db 필요)는 이 시험 밖
        wb.round()
        url, alt, ok = wb.q.execute("select url, alt, last_ok > 0 from peers where node = ?", (wa.me,)).fetchone()
        assert (url, alt, ok) == (f"http://127.0.0.1:{port}", "http://127.0.0.1:9", 1), (url, alt, ok)
        assert wb.q.execute("select count(*) from hindex where node = ?", (wa.me,)).fetchone()[0] == 1
        print("test_alt_fallback: 통과")
    finally:
        win.terminate()
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
