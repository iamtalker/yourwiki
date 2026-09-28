#!/usr/bin/env bash
# 유어위키 리눅스 서버 켜기·끄기 (베타)
#
#   bash server/yourwiki.sh start            # 켜기 (기본: 0.0.0.0:3000 으로 공개, 동기화 자동)
#   bash server/yourwiki.sh stop | status
#   sudo bash server/yourwiki.sh install-service   # systemd 에 등록해 부팅 때 자동 시작
#
# 환경 변수: LISTEN(기본 0.0.0.0:3000), SYNC(auto|queue|off, 기본 auto)
# HTTPS 는 nginx·Caddy 같은 역방향 프록시를 3000번 앞에 두세요.
set -euo pipefail
KIT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$KIT"
LISTEN="${LISTEN:-0.0.0.0:3000}"
SYNC="${SYNC:-auto}"
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
    launch proxy proxy.log python3 scripts/offline_proxy.py assets --listen "$LISTEN" \
      --upstream 127.0.0.1:3001 --queue-db wiki/updater.db
    case "$SYNC" in
      auto)  launch updater updater.log python3 scripts/updater.py wiki --watch ;;
      queue) launch updater updater.log python3 scripts/updater.py wiki --watch --queue-only ;;
    esac
    echo "켰습니다: http://$LISTEN (엔진 준비에 몇 분 걸릴 수 있습니다) · 동기화: $SYNC"
    echo "※ CC BY-NC-SA 2.0 KR · 상업적 이용 금지 · 광고를 붙이거나 상업적으로 운영하면 라이선스 위반입니다."
    ;;
  stop)
    for n in updater proxy engine; do
      running "$n" && kill "$(cat "$RUN/$n.pid")" && echo "$n 껐습니다"
      rm -f "$RUN/$n.pid"
    done
    ;;
  status)
    for n in engine proxy updater; do
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
Environment=LISTEN=$LISTEN SYNC=$SYNC
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
