"""BitTorrent 공용 연결망(메인라인 DHT)에 'ID → 지금 주소'를 적고 찾기 (유어위키 1.2, 표준 라이브러리만 사용).

서버 없이 유어위키끼리 서로 찾기 위해, 토렌트 프로그램들이 전 세계에서 함께 쓰는 DHT 를 빌린다.
BEP 44(서명된 변경 가능 항목)를 쓴다: 내 Ed25519 열쇠로 서명한 작은 값(1000바이트 이하)을
'공개 열쇠 + salt' 로 정해지는 자리에 올려 두면, 내 ID 를 아는 사람은 누구나 찾아 서명을 확인할 수 있다.

- 이 프로그램은 '읽기 전용 노드'(BEP 43, ro=1)로만 참여한다. 남의 요청에 답할 필요가 없어 공유기 뒤에서도 된다.
- 항목은 DHT 에서 약 2시간 뒤 사라지므로 p2p.py 가 30분마다 다시 올린다.
- 값 모양: {"u": 내 P2P 주소, "p": 내가 아는 다른 ID 들(32바이트씩 이어 붙임), "t": 올린 시각}

    d = DHT(); d.put(secret, pub, value_dict, seq); d.get(pub) → (seq, value_dict) 또는 None
"""
import hashlib
import os
import random
import socket
import struct
import time

import ed25519

SALT = b"yourwiki"
BOOTSTRAP = [("router.bittorrent.com", 6881), ("dht.transmissionbt.com", 6881),
             ("router.utorrent.com", 6881), ("dht.libtorrent.org", 25401)]
K = 8            # 가장 가까운 노드 몇 개에 올리고 확인하나
ALPHA = 8        # 한 번에 동시에 물어볼 노드 수
ROUNDS = 12
TIMEOUT = 2.0


# ---------------------------------------------------------------- bencode
def bencode(x):
    if isinstance(x, bool):
        x = int(x)
    if isinstance(x, int):
        return b"i%de" % x
    if isinstance(x, str):
        x = x.encode("utf-8")
    if isinstance(x, (bytes, bytearray)):
        return b"%d:%s" % (len(x), bytes(x))
    if isinstance(x, (list, tuple)):
        return b"l" + b"".join(bencode(i) for i in x) + b"e"
    if isinstance(x, dict):
        items = sorted((k.encode("utf-8") if isinstance(k, str) else k, v) for k, v in x.items())
        return b"d" + b"".join(bencode(k) + bencode(v) for k, v in items) + b"e"
    raise TypeError(f"bencode 할 수 없음: {type(x)}")


def bdecode(data):
    def dec(i):
        c = data[i:i + 1]
        if c == b"i":
            j = data.index(b"e", i)
            return int(data[i + 1:j]), j + 1
        if c == b"l":
            out, i = [], i + 1
            while data[i:i + 1] != b"e":
                v, i = dec(i)
                out.append(v)
            return out, i + 1
        if c == b"d":
            out, i = {}, i + 1
            while data[i:i + 1] != b"e":
                k, i = dec(i)
                v, i = dec(i)
                out[k] = v
            return out, i + 1
        if c.isdigit():
            j = data.index(b":", i)
            n = int(data[i:j])
            if n < 0 or j + 1 + n > len(data):
                raise ValueError("길이가 잘못됨")
            return data[j + 1:j + 1 + n], j + 1 + n
        raise ValueError("bencode 가 아님")
    v, end = dec(0)
    return v


# ---------------------------------------------------------------- BEP 44
def target_of(pub, salt=SALT):
    return hashlib.sha1(pub + salt).digest()


def sign_buffer(v, seq, salt=SALT):
    """BEP 44 가 정한 서명 대상: (salt 가 있으면 '4:salt' + salt) + '3:seqi' + seq + 'e1:v' + bencode(v)."""
    head = (b"4:salt" + bencode(salt)) if salt else b""
    return head + b"3:seqi%de1:v" % seq + bencode(v)


def xor(a, b):
    return int.from_bytes(a, "big") ^ int.from_bytes(b, "big")


def parse_nodes(blob):
    out = []
    for i in range(0, len(blob) - len(blob) % 26, 26):
        nid, ip, port = blob[i:i + 20], socket.inet_ntoa(blob[i + 20:i + 24]), struct.unpack("!H", blob[i + 24:i + 26])[0]
        if port and not ip.startswith(("0.", "127.", "10.", "192.168.")) or os.environ.get("YOURWIKI_DHT_ALLOW_LOCAL"):
            out.append((nid, (ip, port)))
    return out


class DHT:
    def __init__(self, bootstrap=None, timeout=TIMEOUT):
        self.id = os.urandom(20)
        env = os.environ.get("YOURWIKI_DHT_BOOTSTRAP", "")  # 시험용: host:port,host:port
        env_boot = [(h.rsplit(":", 1)[0], int(h.rsplit(":", 1)[1])) for h in env.split(",") if ":" in h]
        self.bootstrap = bootstrap or env_boot or BOOTSTRAP
        self.timeout = timeout
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", 0))

    def close(self):
        self.sock.close()

    def _boot_addrs(self):
        out = []
        for host, port in self.bootstrap:
            try:
                out.append((socket.gethostbyname(host), port))
            except OSError:
                pass
        return out

    def _ask(self, targets, q, args):
        """여러 노드에 같은 질문을 동시에 보내고, 제한 시간 안에 온 답을 {주소: 답} 으로."""
        pending = {}
        for addr in targets:
            t = os.urandom(2)
            msg = {"t": t, "y": "q", "q": q, "a": dict(args, id=self.id), "ro": 1}
            try:
                self.sock.sendto(bencode(msg), addr)
                pending[t] = addr
            except OSError:
                pass
        out, end = {}, time.time() + self.timeout
        while pending and time.time() < end:
            self.sock.settimeout(max(0.01, end - time.time()))
            try:
                data, frm = self.sock.recvfrom(65536)
            except (socket.timeout, OSError):
                break
            try:
                r = bdecode(data)
            except (ValueError, IndexError):
                continue
            if not isinstance(r, dict):
                continue
            addr = pending.pop(r.get(b"t"), None)
            if addr is None:
                continue
            out[addr] = r
        return out

    def _lookup(self, target):
        """target 에 가까운 노드들을 찾아가며 get 을 보낸다. (가장 새 값, 가까운 노드들의 토큰) 을 돌려준다."""
        seen, asked = {}, set()
        for addr in self._boot_addrs():
            seen[addr] = None
        best, tokens = None, {}
        for _ in range(ROUNDS):
            cand = sorted((a for a in seen if a not in asked),
                          key=lambda a: xor(seen[a], target) if seen[a] else (1 << 161))[:ALPHA]
            if not cand:
                break
            asked.update(cand)
            replies = self._ask(cand, "get", {"target": target})
            for addr, r in replies.items():
                a = r.get(b"r")
                if not isinstance(a, dict):
                    # get 을 모르는 노드(부트스트랩 라우터 등)는 find_node 로 이웃만 얻는다
                    rr = self._ask([addr], "find_node", {"target": target}).get(addr, {}).get(b"r")
                    if isinstance(rr, dict):
                        for nid, naddr in parse_nodes(rr.get(b"nodes", b"")):
                            seen.setdefault(naddr, nid)
                    continue
                if isinstance(a.get(b"id"), bytes):
                    seen[addr] = a[b"id"]
                if isinstance(a.get(b"token"), bytes):
                    tokens[addr] = a[b"token"]
                for nid, naddr in parse_nodes(a.get(b"nodes", b"")):
                    seen.setdefault(naddr, nid)
                v, k, sig, seq = a.get(b"v"), a.get(b"k"), a.get(b"sig"), a.get(b"seq")
                if v is not None and isinstance(k, bytes) and isinstance(sig, bytes) and isinstance(seq, int):
                    if target_of(k) == target and ed25519.verify(k, sign_buffer(v, seq), sig):
                        if best is None or seq > best[0]:
                            best = (seq, v)
            # 가장 가까운 K 개를 모두 물어봤으면 끝
            closest = sorted((a for a in seen if seen[a]), key=lambda a: xor(seen[a], target))[:K]
            if closest and all(a in asked for a in closest):
                break
        closest = sorted((a for a in tokens if seen.get(a)), key=lambda a: xor(seen[a], target))[:K]
        return best, {a: tokens[a] for a in closest}

    def get(self, pub):
        """ID(공개 열쇠 32바이트)가 올린 값. (seq, 값) 또는 None."""
        best, _ = self._lookup(target_of(pub))
        return best

    def put(self, secret, pub, value, seq):
        """내 값을 올린다. 올리는 데 성공한 노드 수를 돌려준다."""
        v = value
        if len(bencode(v)) > 1000:
            raise ValueError("DHT 값은 1000바이트를 넘을 수 없음")
        target = target_of(pub)
        best, tokens = self._lookup(target)
        if best and best[0] >= seq:
            seq = best[0] + 1
        sig = ed25519.sign(secret, sign_buffer(v, seq))
        ok = 0
        for addr, token in tokens.items():
            r = self._ask([addr], "put", {"token": token, "v": v, "k": pub, "salt": SALT, "seq": seq, "sig": sig})
            if r.get(addr, {}).get(b"y") == b"r":
                ok += 1
        return ok, seq


def pack_ids(ids):
    return b"".join(bytes.fromhex(i) for i in ids)


def unpack_ids(blob):
    return [blob[i:i + 32].hex() for i in range(0, len(blob) - len(blob) % 32, 32)]


def random_peers(ids, n=20):
    ids = list(ids)
    random.shuffle(ids)
    return ids[:n]
