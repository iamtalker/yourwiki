# README for AI — 유어위키 인수인계 문서

> **이 문서는 AI 작업 세션끼리 이어 달리기를 하기 위한 것이다.**
> 새 세션은 작업을 시작하기 전에 이 문서를 끝까지 읽는다.
> 작업을 마칠 때(커밋·푸시 전)는 맨 아래 「작업 기록」에 새 항목을 **위에 덧붙인다**. 지우지 말고 쌓는다.
> 결정이 바뀌면 옛 내용을 지우지 말고 "(YYYY-MM-DD 바뀜: …)" 로 고쳐 적는다. 이유가 남아야 같은 논의를 되풀이하지 않는다.

## 1. 사용자(저장소 주인)와 일하는 법

- 저장소: `iamtalker/yourwiki`. 주인은 한국어 사용자다. **답은 한국어로 한다.**
- 주인은 PC 를 늘 쓰지 못한다(밖에서 일함, 휴대폰으로 지시하는 일이 많다). 그래서 AI 가 끝까지 해 두는 것을 원한다.
- **변경할 때마다 커밋하고 작업 브랜치에 푸시한다**(주인이 확인함). `main` 병합은 주인이 나중에 로컬에서 한 번에 한다.
  AI 가 PR 을 병합하지 않는다(환경이 막기도 한다).
- 주인은 **자기 생각이라고 무비판적으로 받지 말고, 치명적 문제가 있으면 말하라**고 했다. 동의만 하지 말고 위험을 짚는다.
- 주인은 개발 용어보다 결과를 본다. 설명은 짧게, 용어는 풀어서.
- 릴리스(GitHub Release)는 **AI 가 만든다**(2026-10-02 주인 확인: 1.2 를 클라우드 세션이 만든 건 어쩔 수 없었던 것). 자산은 `git archive --prefix=yourwiki/ -o yourwiki-X.Y.zip HEAD`. 판 번호는 `scripts/panel.py` 의 `KIT_VERSION` 과 `CHANGELOG.md`.

## 2. 이 프로젝트의 원칙 (판단이 갈리면 이것으로 정한다)

1. **탈중앙화가 목적이다.** "이 프로젝트는 탈중앙화가 목적인데 거기서 또 서버를 두면 다른 중앙화"(주인).
   → **누군가 계속 관리해야 하는 것(서버·계정·유료 서비스)은 기본값이 될 수 없다.** 있으면 좋은 선택 사항까지만.
   주인 자신의 관리가 필요한 서비스도 안 된다("내 케어가 필요한 서비스는 유어위키라고 볼 수 없다").
2. **나무위키에 부담을 주지 않는다.** robots.txt 준수, 6초에 1건 이하, 같은 문서 24시간에 한 번, 캡차·차단이 보이면 멈춤.
   P2P 도 나무위키로 가는 요청을 늘리지 않는다(검증 요청은 갱신기 몫의 20% 안에서).
3. **CC BY-NC-SA 2.0 KR.** 상업 기능(광고 등)을 넣지 않는다. 원 문서 주소·기여자·라이선스 고지를 지우지 않는다.
4. **사용자에게 드러나는 것(IP 공개, 인터넷 사용량, 공개 주소)은 기본 꺼짐이고, 켜기 전에 무슨 일이 생기는지 알려 준다.**
5. **파이썬 표준 라이브러리만** 쓴다(Windows 임베디드 파이썬에 pip 없이 돌아가야 함). 외부 바이너리는 `sources.json` 에 해시와 함께.
6. **화면·기록(로그)은 한국어**로, 비개발자가 읽을 수 있게.
7. **한 번 고른 설정은 재시작 뒤에도 유지된다**(관리판 `panel.json`, 리눅스 `yourwiki.conf`, Docker 는 compose 환경 변수).
8. 받은 것은 믿지 않는다: 서명·해시·검증 실적으로만 믿는다(주소나 말로 믿지 않음).

## 3. 구조 한눈에

| 포트 | 무엇 | 파일 |
|---|---|---|
| 3100 | 관리판(127.0.0.1 전용) | `scripts/panel.py` (HTML·JS 가 파일 안 `PAGE` 문자열에 있음) |
| 3000 | 사용자에게 보이는 위키(중계 서버: 오프라인 자원 치환, 갱신 단추, 휴대폰 CSS, `/_hub/`) | `scripts/offline_proxy.py` |
| 3001 | openNAMU 위키 엔진(바이너리, `wiki/`) | 설치가 받아 옴 |
| 3002 | 내 P2P 창구(읽기 전용 중계소) | `scripts/hub_server.py --read-only` + `wiki/hub.db` |

- 데이터: `wiki/data.db`(openNAMU SQLite: data, history, data_set, back, other), `wiki/updater.db`(갱신기·P2P 상태), `wiki/p2p_key.json`(내 ID 열쇠).
- 설치: Windows `scripts/install.ps1`, 리눅스 `server/install.sh`, Docker `Dockerfile`·`docker/entrypoint.sh`. 받을 곳과 해시는 `sources.json`(위에서부터 차례로 시도).
- 켜기·끄기: Windows 는 관리판(`유어위키.bat` → `panel_launch.ps1` → `panel.py`), 리눅스 `server/yourwiki.sh start|stop|status|install-service`.
- 갱신기 `scripts/updater.py`: 나무위키 최신판을 따라잡는다(대기열 `queue`, 모드 auto/queue/off).
- 내보내기 `scripts/convert_wiki.py`: 나무마크 → MediaWiki / DokuWiki / Markdown (멀티프로세스).
- 유어위키(openNAMU) 형식 내보내기·가져오기 `scripts/wiki_pack.py`: data·history·data_set·back 표만 담은 SQLite.
  계정·IP·토론 표는 절대 넣지 않는다. 범위 `all` / `changed`(= history 에서 날짜 ≥ 설치한 덤프 날짜(`wiki/edition.json`, 없으면 2026-08-01)
  이고 기록자가 '나무위키 덤프'·'알파위키 덤프'·'유어위키 키트' 가 아닌 판). 가져오기는 내 쪽 마지막 판 날짜보다 새 판만 붙이고,
  위키 엔진이 꺼져 있을 때만(관리판이 막음). 가져올 파일은 `import/` 폴더(관리판이 파일 이름만 받으므로 경로를 넘나들 수 없음).
  가져오기 검증: 마지막 가져온 판의 기록자가 '유어위키 갱신기'·'유어위키 P2P' 이고 요약에 "수정 YYYY-MM-DD hh:mm:ss" 가 있으면
  `updater.db` 의 shared 에 src='import', node='file:파일이름', tier='import' 로 적고, 바꾸기 전 내용은 p2p_backup 에 둔다.
  그러면 P2P 와 같은 `p2p.audit`·`audit_missing`·`ban`·`restore` 가 그대로 검증·되돌리기를 한다(파일은 nodes 표에 넣지 않음:
  DHT 찾기가 16진 ID 만 다루므로. 파일의 404 횟수·거짓 기록은 meta 의 `strikes:file:…`·`bad:file:…`).
  갱신기는 P2P 를 꺼도 검증을 한다(audit 호출이 P2P 여부와 무관).
- 새 판 알림 `scripts/update_check.py`: GitHub 최신 릴리스만 12시간에 한 번 확인. 알리기만 한다(자동 설치 금지).

### P2P (1.2, 서버 없음)

- `scripts/p2p.py` 작업자. 각 위키가 나무위키에서 받은 문서를 Ed25519(`scripts/ed25519.py`, RFC 8032 순수 파이썬)로 서명해 자기 창구에 둔다.
- 서로 찾기: BitTorrent 메인라인 DHT(`scripts/dht.py`, BEP44 변경 가능 항목, 읽기 전용 노드). 값 = `{"u": 주소, "a": 두 번째 주소(선택), "p": 아는 ID들, "t"}`.
  모르는 위키끼리는 공용 열쇠로 쓰는 **게시판 8칸**(`BOARD_SECRET = sha256("yourwiki-p2p-board-v1")`)에 ID 를 적고 읽는다. 친구 ID 없이도 찾는다.
- 창구 여는 방법(`panel.json` 의 `p2p_open`, 리눅스 `P2P_OPEN`):
  - `tunnel`(기본): cloudflared 빠른 터널. IP 숨김. **Cloudflare 무료 서비스에 기대는 것이 남은 가장 큰 중앙 의존.**
  - `direct`: `scripts/direct.py` — 공인 IP 가 바로 있으면 그대로, 아니면 공유기 UPnP 로 포트를 1시간씩 빌림(20분마다 갱신), 공인 IPv6 가 있으면 그것도. 내 IP 가 보인다.
  - `both`: 직접 주소를 먼저, 터널을 두 번째로 올린다. 받는 쪽은 첫 주소가 안 되면 두 번째로 넘어가고 순서를 바꿔 기억한다.
- 사보타주 방지: 친구 > 검증된 ID(검증 5건 통과, 거짓 0) > 수습 ID. 수습 ID 모두 합쳐 시간당 200개. 검증은 나무위키 원문과 비교
  (더 새 수정 시각을 사칭하거나 같은 시각인데 글자가 다르면 차단 → 그 ID 가 준 문서 모두 되돌림). 같은 내용은 처음 올린 ID 만 실적 인정.
- 중계소(`scripts/hub.py`, `hub_server.py`, `server/hub-setup.sh`)는 **선택 사항**. 기본 목록은 비어 있다(원칙 1).

## 4. 시험하는 법

이 클라우드 환경에서는 namu.wiki, 바깥 UDP(DHT), Cloudflare 터널, 실제 공유기에 닿지 않는다. 그래서 가짜로 시험한다.
- `tests/fakedht.py`: 로컬 UDP 가짜 DHT 노드들. `YOURWIKI_DHT_BOOTSTRAP=127.0.0.1:포트`, `YOURWIKI_DHT_ALLOW_LOCAL=1` 과 함께.
- `YOURWIKI_P2P_ALLOW_LOCAL=1`: 127.0.0.1 주소도 남의 창구로 받아 준다(한 컴퓨터에서 위키 여러 개).
- `YOURWIKI_UPNP_LOCATION=http://…/desc.xml`: SSDP 없이 가짜 공유기로 UPnP 시험(`tests/test_direct.py`).
- `python3 tests/run_all.py` 로 한 번에 돌린다(아래 목록은 추가될 때마다 여기 적는다).
  - `test_direct.py`: UPnP 포트 빌리기·충돌 시 다음 포트·영구만 되는 공유기·CGNAT 거절·주소 규칙
  - `test_alt_fallback.py`: 두 위키, 첫 주소가 막혔을 때 두 번째 주소로 받고 순서를 바꿔 기억하는지
  - `test_pack.py`: openNAMU 형식 '이후 바뀐 것만' 내보내기 → 되돌린 위키에 가져오기 → 같아지는지, 다시 넣으면 건너뛰는지, 계정 표가 안 나가는지,
    가져온 문서가 검증 대기에 들어가는지, 검증 통과·거짓(→ 그 파일에서 온 것 모두 되돌림)이 맞게 되는지
- 관리판 화면은 playwright(`/opt/pw-browsers/chromium`)로 찍어 확인한다. 설치된 위키 없이도 `wiki/data.db` 만 있으면 관리판은 뜬다.
- 모두 `python3 -m pyflakes scripts/*.py`, `bash -n server/*.sh docker/entrypoint.sh` 를 통과해야 한다.

## 5. 환경에서 알게 된 것 (시간 아끼기)

- `pkill -f 패턴` 은 그 패턴이 들어간 내 셸까지 죽인다(종료 코드 144). pid 파일이나 /proc 검사로 끈다.
- 관리판 코드를 고친 뒤 시험할 때 **예전 관리판 프로세스가 3100 을 계속 잡고 있지 않은지** 확인한다(새 코드가 안 뜬 것처럼 보임).
- 리눅스에서는 관리판의 `cleanup_leftovers()` 가 아무것도 안 한다(Windows 전용). 강제 종료 시험 뒤엔 남은 엔진을 직접 끈다.
- 환경의 안전 장치가 막는 것: PR 병합, `.claude/settings.json` 스스로 고치기, 터널 주소로 바깥 접속 확인. 우회하지 않는다.

## 6. 남은 일 (로드맵) — 끝내면 지우지 말고 [완료] 로 표시

- [ ] 실제 PC 에서 P2P 시험(진짜 DHT·Cloudflare·공유기 UPnP). 주인 몫.
- [ ] `main` 병합과 v1.2 GitHub 릴리스. 주인 몫.
- [x] PR 설명(작업 브랜치 → main) 최신으로 고침(2026-09-29). 브랜치에 큰 변경을 더하면 PR 설명도 함께 고친다.
- [ ] Docker 에서 직접 연결은 `network_mode: host` 일 때만 된다. 안내만 되어 있다.
- [ ] IPv6 는 공유기 방화벽이 막는지 스스로 알 수 없다(바깥에서 확인해 줄 곳이 없음). 지금은 두 번째 주소로 넘어가는 것으로 버틴다.
- [x] 파일로 가져온 문서도 P2P 처럼 나무위키와 맞춰 보는 검증(2026-09-29).
- [ ] (제안만) 수습 ID 한도(모두 합쳐 시간당 200개)는 주인이 "괜찮다"고 함. 바꾸자고 먼저 제안하지 말 것.
- [x] 유어위키(openNAMU) 형식 내보내기·가져오기, 범위 '전체 / 설치한 판 이후 바뀐 문서만'(2026-09-29)
- [x] 관리판 칸 접기(2026-09-29)
- [x] 설정 유지(2026-09-29)
- [x] "문서 ?개" 표시 고침(2026-09-29)
- [x] Cloudflare 의존 줄이기: 직접 연결(UPnP·공인 IP·IPv6) 선택 사항(2026-09-29)

## 7. 작업 기록 (새 항목을 위에 덧붙인다)

### 2026-09-29 (6)
- 형제 프로젝트 **애니위키 키트**(`iamtalker/anywiki-kit`)를 만듦: 이 저장소 1.2 에서 나무위키 전용 기능(데이터·동기화·P2P·중계소·가져오기 검증)을 뺀 범용판.
  공통 부분(관리판·중계 서버·내보내기·가져오기)을 고치면 양쪽에 같이 반영할지 확인할 것. openNAMU 형식 파일은 서로 주고받을 수 있다(메타 표 `yourwiki_pack`).
- 관리판 버그 수정: 설치 기록과 가져오기 기록이 같은 id(`ilog`)를 써서 서로 덮어쓰던 것 → `inslog`·`implog`.

### 2026-09-29 (5)
- 가져온 문서 검증(위 3절). 관리판 가져오기 칸에 '검증 대기·확인함·되돌린 파일' 표시.
- 문서 정리: README(2026판 기준으로 받는 것·한계·공개 방법 고침, 내보내기 절 둘을 하나로, 설정 유지·yourwiki.conf 안내),
  CHANGELOG 1.2 를 기능별로 다시 묶음(중간에 바뀐 설계는 빼고 최종 모습만), NOTICE 에 cloudflared, 서버 안내 페이지에 yourwiki.conf.
- PR 설명을 1.2 전체 내용으로 고침.

### 2026-09-29 (4)
- 내보내기를 '기간 선택(날짜 두 칸)'으로 바꿨다가(커밋 7c07db6) 주인이 원래 방식이 더 낫다고 해서 되돌림.
  **확정: 내보내기·가져오기 모두 '전체 / 설치한 판 이후 바뀐 문서만', 고른 범위는 기억.** 다시 기간 선택으로 바꾸자는 제안은 하지 말 것
  (필요하면 7c07db6 에 구현이 있다: base_end·default_period·--since/--until).
- 되돌리면서도 관리판 do_POST 의 지역 `import re` 제거(다른 분기에서 re 를 쓰면 NameError 가 나는 함정)는 유지.

### 2026-09-29 (2) — 작업 브랜치 `claude/inspiring-franklin-vq2rql`
- 유어위키(openNAMU) 형식 내보내기·가져오기(`wiki_pack.py`), 관리판 '내보내기'(이름 바꿈)·'가져오기' 칸, 범위 선택(전체 / 설치한 판 이후 바뀐 것만)을
  모든 내보내기에 적용(`convert_wiki.py --range`). 범위 선택은 `panel.json` 의 `export_range`·`import_range` 로 기억.
- 확인한 것: '전체' 파일을 openNAMU 엔진이 data.db 로 그대로 열어 문서를 보여 줌.
- 주인이 물음: "P2P 켜짐·꺼짐 둘 다 있나" → 있음(관리판 P2P 칸 '끄기(기본)/켜기', 리눅스·Docker `P2P=on|off`).

### 2026-09-29 — 작업 브랜치 `claude/inspiring-franklin-vq2rql`
- 관리판: 기능별 칸을 접어 둠. 상태·위키(켜기·끄기)만 펼침. 접힌 칸 제목 옆에 한 줄 요약. 펼침 상태는 브라우저가 기억.
- 설정 유지: `panel.json` 을 임시 파일 → 바꿔 끼우기 + `.bak`. 위키 켜 둔 상태(`wiki_on`)와 공개(`public`)도 기억해 관리판을 다시 열면 다시 켬.
  리눅스는 환경 변수를 `yourwiki.conf` 에 기억(source 하지 않고 KEY=값 만 읽음), systemd 서비스도 그 파일을 따름.
- 문서 수: 엔진 준비 뒤에도 1분마다 읽고 마지막 수를 기억.
- 직접 연결(`direct.py`)과 DHT 두 번째 주소(`a`), 받는 쪽 자동 전환. `public_url_ok` 가 `http://공인IP:1024이상` 도 받음(내용은 서명·해시로 확인하므로 안전).
  `hub_server.py` 가 `[::]:포트`(IPv4+IPv6 한 번에) 를 받음, 요청마다 60초 시간 제한.
- 이 문서(README_FOR_AI.md)와 `CLAUDE.md`, `tests/` 를 만듦.

### 2026-09-28 ~ 29 — 이전 세션 요약 (이 문서가 생기기 전)
- 1.1.1: 리눅스·Docker 도 archive.org 실패 시 Hugging Face 로, 2021판 MD5 검증.
- 1.2: P2P 공유 → 중계소 방식 → **서버 없는 방식(DHT + 각 위키 창구)** 으로 바뀜(원칙 1 때문). 사보타주 방지(검증·차단·되돌리기).
  MediaWiki·DokuWiki·Markdown 내보내기, 진행률·남은 시간 한국어 표시, 휴대폰 화면, 새 판 알림, P2P 칸 단순화(설명 + 켜기·끄기, 나머지는 고급 설정).
- 논의에서 정해진 것: Oracle 무료 서버 같은 중계소를 기본으로 두지 않는다(원칙 1). 친구 ID 없이도 찾게(BitTorrent 처럼).
  사용자가 자기 ID 를 알 필요도 없다(고급 설정에만).

### 2026-10-02 — 1.2.1 (로컬 Windows 세션)
- **상시 규칙(주인)**: 유어위키에서 **공통 기능**(관리판·중계 서버·내보내기·`wiki_pack`·터널·설치 스크립트·Docker·새 판 알림 등)을 고치면 형제 프로젝트
  `iamtalker/Anywiki-kit` 에도 같은 수정을 적용한다(로컬 `C:\claude program\anywiki-kit`). 나무위키 전용 기능(데이터·갱신기·P2P·중계소)은 해당 없음. 포트는 애니위키가 4000 대.
- 임베디드 파이썬 sys.path 문제와 `file:` URI(공백·한글 경로) 문제를 고쳐 1.2.1 로 릴리스. `wiki_pack` 은 ATTACH 에 URI 를 쓰므로 dst 연결도 `uri=True`.
- 시험은 반드시 `PYTHONUTF8=1 tools/python/python.exe tests/run_all.py` (cp949 콘솔에서 하위 프로세스 출력 해석이 깨짐). 통과.

## 이전 메모 (2026-10-02, 위 1.2.1 에서 해결됨)
- **내보내기가 안 되던 원인**: 키트의 임베디드 파이썬(`tools/python`)은 `._pth` 때문에 스크립트 폴더를 `sys.path` 에 넣지 않는다.
  `convert_wiki.py`, `wiki_pack.py`(직접 실행됨), `dht.py`, `hub.py` 에 `sys.path.insert(0, 스크립트 폴더)` 를 추가함(아직 커밋 전).
  → MediaWiki·DokuWiki·Markdown 내보내기는 20문서 시험 통과.
- **남은 버그**: `wiki_pack.py export` 가 `sqlite3.OperationalError: unable to open database: file:C:\...\data.db?mode=ro` 로 실패.
  Windows 경로(역슬래시·공백·한글)를 그대로 `file:` URI 에 넣어서다. `pathlib.Path(p).resolve().as_uri() + "?mode=ro"` 로 고칠 것
  (같은 패턴이 다른 스크립트에도 있는지 `grep -n "file:{" scripts/*.py` 로 확인).
- 교훈: 새 스크립트는 반드시 `tools/python/python.exe`(임베디드)로 시험한다. 일반 파이썬에서는 이 문제가 드러나지 않는다.
- 고친 뒤: 커밋 → push → 1.2 릴리스(`yourwiki-1.2.zip` 자산 포함, CHANGELOG 1.2 의 "아직 릴리스 전" 제거, `panel.py` 의 KIT_VERSION 1.2).
