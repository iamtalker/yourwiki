"""시험용 가짜 DHT: 로컬 UDP 노드 여러 개. ping/find_node/get/put(BEP44 서명·seq 검사)."""
import os, socket, struct, sys, threading
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import dht, ed25519
class Node(threading.Thread):
    def __init__(self, net):
        super().__init__(daemon=True); self.id = os.urandom(20); self.net = net; self.store = {}
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); self.sock.bind(("127.0.0.1", 0)); self.addr = self.sock.getsockname()
    def nodes(self, target):
        close = sorted(self.net, key=lambda n: dht.xor(n.id, target))[:8]
        return b"".join(n.id + socket.inet_aton(n.addr[0]) + struct.pack("!H", n.addr[1]) for n in close)
    def run(self):
        while True:
            data, frm = self.sock.recvfrom(65536)
            try: m = dht.bdecode(data)
            except Exception: continue
            q, a, t = m.get(b"q"), m.get(b"a", {}), m.get(b"t")
            r = {"id": self.id}
            if q == b"find_node": r["nodes"] = self.nodes(a[b"target"])
            elif q == b"get":
                tg = a[b"target"]; r["nodes"] = self.nodes(tg); r["token"] = b"tok" + frm[0].encode()
                if tg in self.store: seq, v, k, sig = self.store[tg]; r.update(seq=seq, v=v, k=k, sig=sig)
            elif q == b"put":
                k, v, seq, sig, salt = a[b"k"], a[b"v"], a[b"seq"], a[b"sig"], a.get(b"salt", b"")
                tg = dht.target_of(k, salt)
                ok = a.get(b"token") == b"tok" + frm[0].encode() and ed25519.verify(k, dht.sign_buffer(v, seq, salt), sig) \
                     and (tg not in self.store or self.store[tg][0] < seq)
                if not ok:
                    self.sock.sendto(dht.bencode({"t": t, "y": "e", "e": [203, "bad"]}), frm); continue
                self.store[tg] = (seq, v, k, sig)
            elif q != b"ping": continue
            self.sock.sendto(dht.bencode({"t": t, "y": "r", "r": r}), frm)
def start(n=20):
    net = []
    for _ in range(n): net.append(Node(net))
    for x in net: x.start()
    return net
