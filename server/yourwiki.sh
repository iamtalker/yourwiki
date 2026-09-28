#!/usr/bin/env bash
# 유어위키 리눅스 서버 켜기·끄기 (베타)
#
#   bash server/yourwiki.sh start            # 켜기 (기본: 0.0.0.0:3000 으로 공개, 동기화 자동)
#   bash server/yourwiki.sh stop | status
#   sudo bash server/yourwiki.sh install-service   # systemd 에 등록해 부팅 때 자동 시작
#
# 환경 변수: LISTEN(기본 0.0.0.0:3000), SYNC(auto|queue|off, 기본 auto)
#            P2P(on|off, 기본 off): 다른 유어위키와 받은 문서 나누기
#            P2P_URL: 다른 위키가 나를 찾아올 공개 주소(https://...), P2P_PEERS: 믿는 피어 주소(쉼표로 구분)
# HTTPS 는 nginx·Caddy 같은 역방향 프록시를 3000번 앞에 두세요.
set -euo pipefail
KIT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$KIT"
LISTEN="${LISTEN:-0.0.0.0:3000}"
SYNC="${SYNC:-auto}"
P2P="${P2P:-off}"
RUN="$KIT/run"; mkdir -p "$RUN"

running() { [ -f "$RUN/$1.pid" ] && kill -0 "$(cat "$RUN/$1.pid")" 2>/dev/null; }
launch() { # 이름 로그 명령...
  local name=$1 log=$2; shift 2
  running "$name" && return
  nohup "$@" >>"$RUN/$log" 2>&1 & echo $! >"$RUN/$name.pid"
}

case "${1:-}" in
  start)
    [ -f wiki/data.db ] || { echo "아직 설치되지 않았습니다: bash server/install.sh"; exit 1; }
    (cd wiki && launch engine server.log ./main.bin 3001 --localhost)
    export PYTHONUTF8=1
    P2P_ARGS=()
    if [ "$P2P" = on ]; then
      P2P_ARGS=(--p2p)
      PEER_ARGS=(); IFS=',' read -ra PS <<< "${P2P_PEERS:-}"
      for p in "${PS[@]}"; do [ -n "$p" ] && PEER_ARGS+=(--peer "$p"); done
      launch p2p p2p.log python3 scripts/p2p.py wiki --watch --self-url "${P2P_URL:-}" "${PEER_ARGS[@]}"
    fi
    launch proxy proxy.log python3 scripts/offline_proxy.py assets --listen "$LISTEN" \
      --upstream 127.0.0.1:3001 --queue-db wiki/updater.db "${P2P_ARGS[@]}"
    case "$SYNC" in
      auto)  launch updater updater.log python3 scripts/updater.py wiki --watch "${P2P_ARGS[@]}" ;;
      queue) launch updater updater.log python3 scripts/updater.py wiki --watch --queue-only "${P2P_ARGS[@]}" ;;
    esac
    echo "켰습니다: http://$LISTEN (엔진 준비에 몇 분 걸릴 수 있습니다) · 동기화: $SYNC · P2P: $P2P"
    echo "※ CC BY-NC-SA 2.0 KR · 상업적 이용 금지 · 광고를 붙이거나 상업적으로 운영하면 라이선스 위반입니다."
    ;;
  stop)
    for n in p2p updater proxy engine; do
      running "$n" && kill "$(cat "$RUN/$n.pid")" && echo "$n 껐습니다"
      rm -f "$RUN/$n.pid"
    done
    ;;
  status)
    for n in engine proxy updater p2p; do
      if running "$n"; then echo "● $n 실행 중"; else echo "○ $n 꺼짐"; fi
    done
    tail -n 5 "$RUN/updater.log" 2>/dev/null || true
    ;;
  install-service)
    [ "$(id -u)" = 0 ] || { echo "sudo 로 실행하세요"; exit 1; }
    USER_NAME="${SUDO_USER:-root}"
    cat >/etc/systemd/system/yourwiki.service <<UNIT
[Unit]
Description=YourWiki (나무위키 글을 담은 위키)
After=network-online.target

[Service]
Type=forking
User=$USER_NAME
Environment=LISTEN=$LISTEN SYNC=$SYNC P2P=$P2P "P2P_URL=${P2P_URL:-}" "P2P_PEERS=${P2P_PEERS:-}"
ExecStart=/usr/bin/env bash $KIT/server/yourwiki.sh start
ExecStop=/usr/bin/env bash $KIT/server/yourwiki.sh stop
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
UNIT
    systemctl daemon-reload && systemctl enable --now yourwiki
    echo "등록했습니다: systemctl status yourwiki"
    ;;
  *)
    sed -n '2,9p' "$0"
    ;;
esac
