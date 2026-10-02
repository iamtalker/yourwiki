"""8단계-나: 한 줄짜리 매개변수 틀을 되접는다(상위 문서·상세 내용·분류 설명·관련 문서·문서 가져옴).

정의는 param_defs.json(손으로 고른 틀 이름·매개변수 이름)과 param_cands.json(param_a.py 가 센 뼈대와 고정 구멍 값)에서 읽는다.
줄 하나가 뼈대와 같고, 고정 구멍이 같고, 값이 안전하면 `[include(틀:이름, 이름=값, …)]` 로 바꾼다.
안전한 값: 비어 있지 않고, 쉼표·줄바꿈·`)]`·`@`·`[[`·`]]` 가 없고, 앞뒤 공백이 없다(openNAMU include 인자 시험 결과, PLAN.md 참고).
"""
import json
import os

from param_a import skeleton
from units import category_links, scan_state, unit_spans

HERE = os.path.dirname(os.path.abspath(__file__))
UNSAFE = (",", "\n", ")]", "@", "[[", "]]")


def safe_value(v):
    # 값이 `분류:…` 이면 원본에서는 그 링크가 분류 달기로도 쓰였다 → 틀 인자로 넘기면 분류가 빠지므로 접지 않는다
    return bool(v) and v == v.strip() and not v.startswith("분류:") and not any(x in v for x in UNSAFE)


def load_defs():
    """{뼈대: 정의}. 정의 = name, fixed{구멍: 값}, params[(구멍, 매개변수 이름)], body(틀 본문)"""
    cands = {}
    for fn in ("param_cands.json", "param_cands_units.json"):  # 줄 뼈대, 덩어리(상자) 뼈대
        p = os.path.join(HERE, fn)
        if os.path.exists(p):
            for c in json.load(open(p, encoding="utf-8")):
                cands.setdefault(c["skel"], c)
    defs = {}
    for d in json.load(open(os.path.join(HERE, "param_defs.json"), encoding="utf-8")):
        c = cands.get(d["skel"])
        if not c:
            raise SystemExit(f"param_cands.json 에 뼈대가 없음: {d['skel']}")
        if len(c["params"]) != len(d["pnames"]):
            raise SystemExit(f"{d['name']}: 매개변수 구멍 {len(c['params'])}개인데 이름은 {len(d['pnames'])}개")
        fixed = {int(k): v for k, v in c["fixed"].items()}
        params = list(zip(c["params"], d["pnames"]))
        pieces = d["skel"].split("[[@]]")
        names = dict(params)
        body = pieces[0]
        for k in range(len(pieces) - 1):
            body += ("[[" + fixed[k] + "]]") if k in fixed else ("[[@" + names[k] + "@]]")
            body += pieces[k + 1]
        defs[d["skel"]] = {"name": d["name"], "fixed": fixed, "params": params, "body": body, "df": c["df"]}
    return defs


def _make(d, vs):
    """뼈대 정의 d 에 값 vs 를 넣어 include 글을 만든다. 맞지 않거나 안전하지 않으면 None."""
    if any(vs[k] != v for k, v in d["fixed"].items()):
        return None
    args = []
    for k, pname in d["params"]:
        if not safe_value(vs[k]):
            return None
        args.append(f"{pname}={vs[k]}")
    return f"[include({d['name']}, {', '.join(args)})]"


def param_candidates(lines, defs):
    """되접을 후보 [(시작 줄, 끝 줄+1, include 글, 줄어드는 글자 수, 틀 이름)]. 맨 바깥 층의 줄과 덩어리."""
    out = []
    if not any("[[" in ln for ln in lines):
        return out
    for i, j in unit_spans(lines):  # 덩어리(표·상자) 전체가 뼈대와 같은 경우
        u = chr(10).join(lines[i:j])
        if not (60 <= len(u) <= 2000):
            continue
        sk, vs = skeleton(u)
        d = defs.get(sk)
        inc = _make(d, vs) if d else None
        if inc:
            inc += category_links(u)  # 분류는 문서에 남긴다
            out.append((i, j, inc, len(u) - len(inc), d["name"]))
    dep, opn = scan_state(lines)
    for i, ln in enumerate(lines):
        if dep[i] != 0 or opn[i] or "[[" not in ln or not (20 <= len(ln) <= 400):
            continue
        sk, vs = skeleton(ln)
        d = defs.get(sk)
        if not d or any(vs[k] != v for k, v in d["fixed"].items()):
            continue
        args, ok = [], True
        for k, pname in d["params"]:
            if not safe_value(vs[k]):
                ok = False
                break
            args.append(f"{pname}={vs[k]}")
        if ok:
            inc = f"[include({d['name']}, {', '.join(args)})]"
            out.append((i, i + 1, inc, len(ln) - len(inc), d["name"]))
    return out
