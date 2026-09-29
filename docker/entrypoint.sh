#!/usr/bin/env bash
# 컨테이너 시작: 처음이면 설치(엔진·데이터 받기, 해시 검증, 문서 넣기) → 위키 엔진·갱신기·중계 서버 실행
set -euo pipefail
cd /kit

# 위키 색 (중계 서버가 panel.json 의 color 를 읽는다)
if [[ "$COLOR" =~ ^#[0-9a-fA-F]{6}$ ]]; then echo "{\"color\": \"$COLOR\"}" > panel.json; fi

DOCS=$(python3 -c "import sqlite3,os;print(sqlite3.connect('wiki/data.db').execute('select count(*) from data').fetchone()[0] if os.path.exists('wiki/data.db') else 0)" 2>/dev/null || echo 0)
if [ "$DOCS" -lt 1000 ]; then
  echo "== 처음 실행: 설치를 시작합니다 (데이터 5GB 받기와 문서 넣기로 1~2시간 걸립니다)"
  bash server/install.sh "$EDITION"
fi

[ "${UPDATE_NOTICE:-on}" = on ] && python3 scripts/update_check.py --quiet || true   # 새 판이 있을 때만 한 줄
echo "※ 이 데이터는 CC BY-NC-SA 2.0 KR입니다. 상업적 이용은 금지됩니다."
echo "  이 키트를 사용해 광고를 붙이거나 상업적으로 운영하는 것은 라이선스 위반입니다."

(cd wiki && ./main.bin 3001 --localhost) &
P2P_ARGS=(); HUB_ARGS=()
[ "${HUB:-off}" = on ] && HUB_ARGS=(--hub)
if [ "${P2P:-off}" = on ]; then
  P2P_ARGS=(--p2p)
  X=(); IFS=',' read -ra PS <<< "${P2P_HUBS:-}"
  for p in "${PS[@]}"; do [ -n "$p" ] && X+=(--hub "$p"); done
  IFS=',' read -ra PS <<< "${P2P_FRIENDS:-}"
  for p in "${PS[@]}"; do [ -n "$p" ] && X+=(--friend "$p"); done
  echo "== 내 P2P ID: $(python3 scripts/p2p.py wiki --id)"
  WIN_LISTEN=127.0.0.1:3002
  case "${P2P_OPEN:-tunnel}" in   # direct·both 는 network_mode: host 에서만 의미가 있다(공유기 UPnP·공인 IP)
    direct|both) WIN_LISTEN="[::]:3002"; X+=(--direct) ;;
  esac
  python3 scripts/hub_server.py --read-only --listen "$WIN_LISTEN" --db wiki/hub.db &   # 내 P2P 창구(읽기 전용)
  if [ -n "${P2P_URL:-}" ]; then
    X+=(--self-url "$P2P_URL")
  elif [ "${P2P_OPEN:-tunnel}" = direct ]; then
    :
  elif CF=$(python3 scripts/cloudflared.py); then
    "$CF" tunnel --no-autoupdate --url http://127.0.0.1:3002 > /tmp/p2p-tunnel.log 2>&1 &
    X+=(--tunnel-log /tmp/p2p-tunnel.log)
  fi
  python3 scripts/p2p.py wiki --watch ${X[@]+"${X[@]}"} &
fi
case "$SYNC" in
  auto)  python3 scripts/updater.py wiki --watch ${P2P_ARGS[@]+"${P2P_ARGS[@]}"} & ;;
  queue) python3 scripts/updater.py wiki --watch --queue-only ${P2P_ARGS[@]+"${P2P_ARGS[@]}"} & ;;
esac
echo "== 유어위키: http://<서버 주소>:${LISTEN##*:} (엔진 준비에 몇 분 걸릴 수 있습니다) · 동기화: $SYNC · P2P: ${P2P:-off} · 중계소: ${HUB:-off}"
exec python3 scripts/offline_proxy.py assets --listen "$LISTEN" --upstream 127.0.0.1:3001 --queue-db wiki/updater.db ${HUB_ARGS[@]+"${HUB_ARGS[@]}"}
