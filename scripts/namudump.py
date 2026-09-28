"""나무위키 공식 JSON 덤프 스트리밍 리더 (표준 라이브러리만 사용).

덤프는 `[{...},{...},...]` 형태의 거대한 JSON 배열 하나(2021판 8.8GB)라
통째로 읽을 수 없다. 조각 단위로 읽으며 객체를 하나씩 꺼낸다.

    for doc in read_docs(stream): ...   # doc = {namespace, title, text, contributors}
"""
import json
import subprocess
import sys

CHUNK = 1 << 20
_decoder = json.JSONDecoder()


def read_docs(stream):
    """텍스트 스트림에서 JSON 배열의 원소를 하나씩 yield 한다."""
    buf = ""
    pos = 0
    started = False
    eof = False
    while True:
        # 공백·구분자 건너뛰기
        while True:
            while pos < len(buf) and buf[pos] in " \t\r\n,":
                pos += 1
            if pos < len(buf) or eof:
                break
            buf, pos = stream.read(CHUNK), 0
            eof = not buf
        if pos >= len(buf):
            return
        if not started:
            if buf[pos] != "[":
                raise ValueError("JSON 배열이 아닙니다")
            started = True
            pos += 1
            continue
        if buf[pos] == "]":
            return
        # 객체 하나를 완전히 디코드할 수 있을 때까지 더 읽는다
        while True:
            try:
                obj, end = _decoder.raw_decode(buf, pos)
                break
            except json.JSONDecodeError:
                more = stream.read(CHUNK)
                if not more:
                    raise
                buf = buf[pos:] + more
                pos = 0
        yield obj
        pos = end
        if pos > CHUNK:
            buf, pos = buf[pos:], 0


def open_dump(path, seven_zip="7z"):
    """.7z 는 압축을 풀지 않고 7-Zip 표준출력으로 스트리밍, .json 은 그대로 연다."""
    if path.lower().endswith(".7z"):
        proc = subprocess.Popen(
            [seven_zip, "e", "-so", "-r", path, "*.json"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        return open(proc.stdout.fileno(), encoding="utf-8", closefd=False), proc
    return open(path, encoding="utf-8"), None


def scan(path, seven_zip="7z"):
    """네임스페이스별 문서 수와 예시 제목을 출력한다."""
    stream, _ = open_dump(path, seven_zip)
    counts, examples, n = {}, {}, 0
    for doc in read_docs(stream):
        ns = doc["namespace"]
        counts[ns] = counts.get(ns, 0) + 1
        ex = examples.setdefault(ns, [])
        if len(ex) < 4:
            ex.append(doc["title"])
        n += 1
    print("총", n, "개 문서")
    for ns in sorted(counts, key=str):
        print(f"  namespace {ns!r}: {counts[ns]}개  예: {examples[ns]}")


if __name__ == "__main__":
    scan(*sys.argv[1:])
