#!/usr/bin/env bash
# 유어위키 리눅스 서버 설치 (베타: 실제 리눅스 서버에서 아직 충분히 시험하지 않았습니다)
#
#   bash server/install.sh            # 2026판 설치
#   bash server/install.sh 2021       # 2021 공식 덤프 원문 설치
#
# 필요한 것: python3(3.8+), 7z(p7zip-full), curl, sha256sum, 디스크 45GB 이상
# 이미 끝난 단계는 건너뛰므로, 중간에 끊겨도 다시 실행하면 이어서 진행합니다.
set -euo pipefail

KIT="$(cd "$(dirname "$0")/.." && pwd)"
EDITION="${1:-}"
cd "$KIT"
step() { printf '\n== %s\n' "$*"; }
die() { echo "오류: $*" >&2; exit 1; }

step "1/6 필요한 프로그램 확인"
for c in python3 7z curl sha256sum; do
  command -v "$c" >/dev/null || die "$c 이 없습니다. 예) sudo apt install python3 p7zip-full curl coreutils"
done
case "$(uname -m)" in
  x86_64|amd64) ARCH=amd64; ENGINE_SHA=d82832ab3d9dda4ff3cde6a0a6ff3740dfd340ba2a8cb44f9da723b7afd91570 ;;
  aarch64|arm64) ARCH=arm64; ENGINE_SHA=3562bce94fe39f30d5b7688fba467baa4d2acd52b0ae437477275b6784e5c1c5 ;;
  *) die "지원하지 않는 CPU 입니다: $(uname -m)" ;;
esac
FREE_GB=$(df -Pk "$KIT" | awk 'NR==2 {print int($4/1024/1024)}')
[ "$FREE_GB" -ge 45 ] || echo "경고: 디스크 여유가 ${FREE_GB}GB 입니다(45GB 이상 권장)."
echo "  완료 (CPU $ARCH, 디스크 여유 ${FREE_GB}GB)"

step "2/6 위키 엔진(openNAMU) 받기"
mkdir -p wiki data
ENGINE_URL="https://github.com/openNAMU/openNAMU/releases/download/v4.3.6-beta.2/main.$ARCH.bin"
if ! echo "$ENGINE_SHA  wiki/main.bin" | sha256sum -c --status 2>/dev/null; then
  curl -fL -o wiki/main.bin "$ENGINE_URL"
  echo "$ENGINE_SHA  wiki/main.bin" | sha256sum -c --status || die "엔진 파일 해시가 맞지 않습니다"
fi
chmod +x wiki/main.bin
echo "  완료"

# sources.json 에서 판 정보 읽기
read -r FILE SHA DATE URL < <(python3 - "$EDITION" <<'PY'
import json, sys
c = json.load(open("sources.json", encoding="utf-8"))
e = c["editions"][sys.argv[1] or c["default_edition"]]
url = next(s["url"] for s in e["sources"] if s["type"] == "http")
print(e["file"], e.get("sha256", "-"), e["date"], url)
PY
)

step "3/6 나무위키 데이터 받기 ($DATE판)"
verify() {
  if [ "$SHA" != "-" ]; then echo "$SHA  data/$FILE" | sha256sum -c --status 2>/dev/null; else [ -s "data/$FILE" ]; fi
}
if ! verify; then
  curl -fL -C - -o "data/$FILE" "$URL" || die "데이터를 받지 못했습니다. 다시 실행하면 이어서 받습니다"
  verify || die "데이터 해시가 맞지 않습니다. data/$FILE 을 지우고 다시 실행하세요"
fi
echo "  검증 완료"

step "4/6 위키 엔진 초기화"
if [ ! -f wiki/data.db ]; then
  (cd wiki && timeout 60 ./main.bin 3001 --localhost >/dev/null 2>&1 || true)
  [ -f wiki/data.db ] || die "openNAMU 가 DB 를 만들지 못했습니다(3001번 포트를 확인하세요)"
fi
echo "  완료"

step "5/6 문서 넣기 (수십 분)"
COUNT=$(python3 -c "import sqlite3;print(sqlite3.connect('wiki/data.db').execute('select count(*) from data').fetchone()[0])")
if [ "$COUNT" -gt 0 ]; then
  echo "  이미 $COUNT 개 문서가 있습니다. 다시 넣으려면 wiki/ 를 지우고 실행하세요"
else
  PYTHONUTF8=1 python3 scripts/import_dump.py "data/$FILE" wiki --7z 7z --dump-date "$DATE"
fi
for f in extras/*.jsonl.gz; do PYTHONUTF8=1 python3 scripts/import_templates.py "$f" wiki; done
PYTHONUTF8=1 python3 scripts/add_frontpage.py wiki

step "6/6 설치 완료"
cat <<'MSG'
  켜기:        bash server/yourwiki.sh start
  상태·끄기:   bash server/yourwiki.sh status | stop
  부팅 시 자동 시작(systemd): sudo bash server/yourwiki.sh install-service

  처음 켤 때 검색 색인을 만드느라 오래 걸리고 CPU 를 많이 씁니다. 그동안에도 문서는 볼 수 있습니다.
  ※ 이 데이터는 CC BY-NC-SA 2.0 KR입니다. 상업적 이용은 금지됩니다.
     이 키트를 사용해 광고를 붙이거나 상업적으로 운영하는 것은 라이선스 위반입니다.
MSG
