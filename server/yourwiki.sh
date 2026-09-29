#!/usr/bin/env bash
# 유어위키 리눅스 서버 켜기·끄기 (베타)
#
#   bash server/yourwiki.sh start            # 켜기 (기본: 0.0.0.0:3000 으로 공개, 동기화 자동)
#   bash server/yourwiki.sh stop | status
#   sudo bash server/yourwiki.sh install-service   # systemd 에 등록해 부팅 때 자동 시작
#
# 환경 변수: LISTEN(기본 0.0.0.0:3000), SYNC(auto|queue|off, 기본 auto)
#            P2P(on|off, 기본 off): 다른 유어위키와 받은 문서 나누기(서버 없이, 공용 연결망 DHT 로 서로 찾음)
#            P2P_FRIENDS: 친구 ID(쉼표로 구분)
#            P2P_URL: 내 P2P 창구(127.0.0.1:3002)로 오는 고정 주소가 있으면 적는다. 비우면 임시 공개 주소를 자동으로 만든다
#            P2P_HUBS: 중계소 주소(선택, 쉼표로 구분)
#            UPDATE_NOTICE(on|off, 기본 on): GitHub 에 새 판이 나왔는지 켤 때 알려 주기(알리기만 함)
#            HUB(on|off, 기본 off): 이 서버를 중계소로도 연다(/_hub/, 고정 도메인 + HTTPS 뒤에서)
# 한 번 준 값은 yourwiki.conf 에 기억되어 다음에 그냥 start 해도 그대로 쓴다. 바꾸려면 새 값을 주고 start.
#   예) P2P=on bash server/yourwiki.sh start   → 다음부터 bash server/yourwiki.sh start 만 해도 P2P 켜짐
# HTTPS 는 nginx·Caddy 같은 역방향 프록시를 3000번 앞에 두세요.
set -euo pipefail
KIT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$KIT"
CONF="$KIT/yourwiki.conf"
VARS=(LISTEN SYNC P2P P2P_FRIENDS P2P_URL P2P_HUBS UPDATE_NOTICE HUB)
# 기억해 둔 설정 읽기(이번에 직접 준 값이 우선). source 하지 않고 KEY=값 줄만 읽는다
if [ -f "$CONF" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    k=${line%%=*}; v=${line#*=}
    case " ${VARS[*]} " in *" $k "*) ;; *) continue ;; esac
    [ -n "${!k+x}" ] || printf -v "$k" '%s' "$v"
  done < "$CONF"
fi
save_conf() {
  local k tmp="$CONF.tmp"
  : > "$tmp"
  for k in "${VARS[@]}"; do [ -n "${!k+x}" ] && printf '%s=%s\n' "$k" "${!k}" >> "$tmp"; done
  mv -f "$tmp" "$CONF"
}
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
    save_conf || echo "설정을 yourwiki.conf 에 기억하지 못했습니다(권한 확인)"
    (cd wiki && launch engine server.log ./main.bin 3001 --localhost)
    export PYTHONUTF8=1
    P2P_ARGS=(); HUB_ARGS=()
    [ "${HUB:-off}" = on ] && HUB_ARGS=(--hub)
    if [ "$P2P" = on ]; then
      P2P_ARGS=(--p2p)
      X=(); IFS=',' read -ra PS <<< "${P2P_HUBS:-}"
      for p in "${PS[@]}"; do [ -n "$p" ] && X+=(--hub "$p"); done
      IFS=',' read -ra PS <<< "${P2P_FRIENDS:-}"
      for p in "${PS[@]}"; do [ -n "$p" ] && X+=(--friend "$p"); done
      # 내 P2P 창구(읽기 전용): 서명된 문서 묶음만 나가고 위키 화면은 나가지 않는다
      launch p2pwin p2p-window.log python3 scripts/hub_server.py --read-only --listen 127.0.0.1:3002 --db wiki/hub.db
      if [ -n "${P2P_URL:-}" ]; then
        X+=(--self-url "$P2P_URL")
      elif CF=$(python3 scripts/cloudflared.py); then
        : > "$RUN/p2p-tunnel.log"
        launch p2ptunnel p2p-tunnel.log "$CF" tunnel --no-autoupdate --url http://127.0.0.1:3002
        X+=(--tunnel-log "$RUN/p2p-tunnel.log")
      else
        echo "P2P 창구 공개 주소를 만들지 못했습니다(받기만 합니다)."
      fi
      launch p2p p2p.log python3 scripts/p2p.py wiki --watch ${X[@]+"${X[@]}"}
      echo "내 P2P ID: $(python3 scripts/p2p.py wiki --id)"
    fi
    launch proxy proxy.log python3 scripts/offline_proxy.py assets --listen "$LISTEN" \
      --upstream 127.0.0.1:3001 --queue-db wiki/updater.db ${HUB_ARGS[@]+"${HUB_ARGS[@]}"}
    case "$SYNC" in
      auto)  launch updater updater.log python3 scripts/updater.py wiki --watch ${P2P_ARGS[@]+"${P2P_ARGS[@]}"} ;;
      queue) launch updater updater.log python3 scripts/updater.py wiki --watch --queue-only ${P2P_ARGS[@]+"${P2P_ARGS[@]}"} ;;
    esac
    echo "켰습니다: http://$LISTEN (엔진 준비에 몇 분 걸릴 수 있습니다) · 동기화: $SYNC · P2P: $P2P · 중계소: ${HUB:-off}"
    echo "※ CC BY-NC-SA 2.0 KR · 상업적 이용 금지 · 광고를 붙이거나 상업적으로 운영하면 라이선스 위반입니다."
    [ "${UPDATE_NOTICE:-on}" = on ] && python3 scripts/update_check.py --quiet || true   # 새 판이 있을 때만 한 줄
    ;;
  stop)
    for n in p2p p2ptunnel p2pwin updater proxy engine; do
      running "$n" && kill "$(cat "$RUN/$n.pid")" && echo "$n 껐습니다"
      rm -f "$RUN/$n.pid"
    done
    ;;
  status)
    for n in engine proxy updater p2p p2pwin p2ptunnel; do
      if running "$n"; then echo "● $n 실행 중"; else echo "○ $n 꺼짐"; fi
    done
    tail -n 5 "$RUN/updater.log" 2>/dev/null || true
    [ "${UPDATE_NOTICE:-on}" = on ] && python3 scripts/update_check.py || true
    ;;
  install-service)
    [ "$(id -u)" = 0 ] || { echo "sudo 로 실행하세요"; exit 1; }
    USER_NAME="${SUDO_USER:-root}"
    save_conf && chown "$USER_NAME" "$CONF"   # 설정은 yourwiki.conf 에서 읽으므로 서비스 파일에 박지 않는다
    cat >/etc/systemd/system/yourwiki.service <<UNIT
[Unit]
Description=YourWiki (나무위키 글을 담은 위키)
After=network-online.target

[Service]
Type=forking
User=$USER_NAME
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
    sed -n '2,17p' "$0"
    ;;
esac
