"""(개발용) 원격 SQLite 파일에서 필요한 페이지만 HTTP Range 로 받아 행을 읽는다.

2026 덤프(수십 GB)를 다 받기 전에 문서 몇 개를 꺼내 변환기를 개발·채점하려고 만들었다.
지원: 테이블 b-tree(rowid 조회), 인덱스 b-tree(ix_title 로 제목 조회), 오버플로 페이지.

    db = RemoteSQLite(url)
    row = db.get_by_title("대한민국")   # {'id':..., 'title':..., 'html': bytes, ...}
"""
import struct
import urllib.request

COLUMNS = ["id", "title", "md", "html", "md_len", "html_len", "text_len", "categories",
           "images", "links", "last_modified", "rendered_at"]


def varint(b, i):
    v = 0
    for k in range(9):
        c = b[i + k]
        if k == 8:
            return (v << 8) | c, i + 9
        v = (v << 7) | (c & 0x7F)
        if c < 0x80:
            return v, i + k + 1
    raise ValueError


class RemoteSQLite:
    def __init__(self, url):
        self.url = url
        self.cache = {}
        head = self._range(0, 100)
        self.page_size = struct.unpack(">H", head[16:18])[0] or 65536
        self.usable = self.page_size - head[20]
        self.fetched = 0
        schema = self._read_table(1)
        self.roots = {row[1]: row[3] for row in schema}  # name -> rootpage

    def _range(self, start, length):
        req = urllib.request.Request(self.url, headers={"Range": f"bytes={start}-{start + length - 1}"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read()

    def page(self, n):
        if n not in self.cache:
            self.cache[n] = self._range((n - 1) * self.page_size, self.page_size)
            self.fetched += self.page_size
        return self.cache[n]

    # ---- 레코드
    def _payload(self, page, i, total, is_table):
        u = self.usable
        x = u - 35 if is_table else ((u - 12) * 64 // 255) - 23
        if total <= x:
            return page[i:i + total]
        m = ((u - 12) * 32 // 255) - 23
        k = m + (total - m) % (u - 4)
        local = k if k <= x else m
        data = bytearray(page[i:i + local])
        nxt = struct.unpack(">I", page[i + local:i + local + 4])[0]
        while nxt and len(data) < total:
            p = self.page(nxt)
            nxt = struct.unpack(">I", p[:4])[0]
            data += p[4:4 + min(u - 4, total - len(data))]
        return bytes(data)

    @staticmethod
    def _record(p):
        hlen, i = varint(p, 0)
        types = []
        while i < hlen:
            t, i = varint(p, i)
            types.append(t)
        out, j = [], hlen
        for t in types:
            if t == 0:
                out.append(None)
            elif t in (1, 2, 3, 4, 5, 6):
                n = {1: 1, 2: 2, 3: 3, 4: 4, 5: 6, 6: 8}[t]
                out.append(int.from_bytes(p[j:j + n], "big", signed=True))
                j += n
            elif t == 7:
                out.append(struct.unpack(">d", p[j:j + 8])[0])
                j += 8
            elif t in (8, 9):
                out.append(t - 8)
            else:
                n = (t - 12) // 2 if t % 2 == 0 else (t - 13) // 2
                v = p[j:j + n]
                out.append(bytes(v) if t % 2 == 0 else v.decode("utf-8", "replace"))
                j += n
        return out

    def _cells(self, n):
        p = self.page(n)
        h = 100 if n == 1 else 0
        kind = p[h]
        count = struct.unpack(">H", p[h + 3:h + 5])[0]
        hdr = 12 if kind in (2, 5) else 8
        right = struct.unpack(">I", p[h + 8:h + 12])[0] if kind in (2, 5) else None
        ptrs = [struct.unpack(">H", p[h + hdr + 2 * k:h + hdr + 2 * k + 2])[0] for k in range(count)]
        return p, kind, ptrs, right

    # ---- 테이블 b-tree
    def _read_table(self, root):
        p, kind, ptrs, right = self._cells(root)
        rows = []
        if kind == 13:
            for c in ptrs:
                total, i = varint(p, c)
                rowid, i = varint(p, i)
                rows.append(self._record(self._payload(p, i, total, True)))
        else:
            for c in ptrs:
                rows += self._read_table(struct.unpack(">I", p[c:c + 4])[0])
            rows += self._read_table(right)
        return rows

    def get_rowid(self, rowid, root=None):
        n = root or self.roots["docs"]
        while True:
            p, kind, ptrs, right = self._cells(n)
            if kind == 13:
                for c in ptrs:
                    total, i = varint(p, c)
                    rid, i = varint(p, i)
                    if rid == rowid:
                        rec = self._record(self._payload(p, i, total, True))
                        rec[0] = rid
                        return dict(zip(COLUMNS, rec))
                return None
            nxt = right
            for c in ptrs:
                child = struct.unpack(">I", p[c:c + 4])[0]
                key, _ = varint(p, c + 4)
                if rowid <= key:
                    nxt = child
                    break
            n = nxt

    # ---- 인덱스 b-tree (ix_title: title -> rowid)
    def title_to_rowid(self, title):
        n = self.roots["ix_title"]
        while True:
            p, kind, ptrs, right = self._cells(n)
            nxt = right
            for c in ptrs:
                j = c + 4 if kind == 2 else c
                total, i = varint(p, j)
                key_title, rid = self._record(self._payload(p, i, total, False))
                if title == key_title:
                    return rid
                if title < key_title:
                    nxt = struct.unpack(">I", p[c:c + 4])[0] if kind == 2 else None
                    break
            if kind == 10 or nxt is None:
                return None
            n = nxt

    def get_by_title(self, title):
        rid = self.title_to_rowid(title)
        return self.get_rowid(rid) if rid else None
