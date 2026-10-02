#!/usr/bin/env bash
# 유어위키 중계소 설치 (Oracle Cloud 무료 서버 등, Ubuntu·Oracle Linux)
#
#   git clone https://github.com/iamtalker/yourwiki && cd yourwiki
#   sudo bash server/hub-setup.sh            # 기본 포트 8080
#   sudo PORT=9000 bash server/hub-setup.sh  # 포트 바꾸기
#
# 하는 일: 중계소를 systemd 서비스로 등록(부팅 때 자동 시작) → 서버 안 방화벽에서 포트 열기 → 주소 알려 주기.
# Oracle Cloud 는 서버 밖에도 방화벽(보안 목록)이 있어서, 콘솔에서 이 포트를 한 번 열어야 합니다(README 참고).
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "sudo 로 실행하세요: sudo bash server/hub-setup.sh"; exit 1; }
KIT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${PORT:-8080}"
DATA=/var/lib/yourwiki-hub
command -v python3 >/dev/null || { apt-get install -y python3 2>/dev/null || dnf install -y python3; }

echo "== 1/3 중계소 서비스 등록"
id yourwiki-hub >/dev/null 2>&1 || useradd --system --home "$DATA" --shell /usr/sbin/nologin yourwiki-hub
mkdir -p "$DATA" && chown yourwiki-hub "$DATA"
cat >/etc/systemd/system/yourwiki-hub.service <<UNIT
[Unit]
Description=YourWiki hub (유어위키 중계소)
After=network-online.target

[Service]
User=yourwiki-hub
Environment=PYTHONUTF8=1
ExecStart=/usr/bin/env python3 $KIT/scripts/hub_server.py --listen 0.0.0.0:$PORT --db $DATA/hub.db
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now yourwiki-hub
sleep 2
systemctl is-active --quiet yourwiki-hub || { journalctl -u yourwiki-hub -n 20 --no-pager; exit 1; }
echo "  완료"

echo "== 2/3 서버 안 방화벽에서 $PORT 번 포트 열기"
if command -v firewall-cmd >/dev/null && firewall-cmd --state >/dev/null 2>&1; then   # Oracle Linux
  firewall-cmd --permanent --add-port="$PORT/tcp" && firewall-cmd --reload
elif command -v iptables >/dev/null; then                                            # Ubuntu (Oracle 이미지는 기본으로 막혀 있음)
  iptables -C INPUT -p tcp --dport "$PORT" -j ACCEPT 2>/dev/null || iptables -I INPUT 1 -p tcp --dport "$PORT" -j ACCEPT
  if command -v netfilter-persistent >/dev/null; then netfilter-persistent save; else
    mkdir -p /etc/iptables && iptables-save > /etc/iptables/rules.v4; fi
fi
echo "  완료"

echo "== 3/3 확인"
curl -fsS "http://127.0.0.1:$PORT/_hub/hello" && echo
IP=$(curl -fsS -m 5 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')
cat <<MSG

  중계소가 켜졌습니다.  주소: http://$IP:$PORT
  1) Oracle Cloud 콘솔에서 이 서버의 보안 목록(Security List)에 TCP $PORT 받기 규칙을 추가하세요(README 참고).
  2) 유어위키 관리판 → P2P 공유 → '중계소 주소'에 위 주소를 적으세요.
  상태: systemctl status yourwiki-hub · 기록: journalctl -u yourwiki-hub -f
  문서마다 서명과 해시가 붙어 있어 https 가 아니어도 중계소를 거치며 내용이 바뀌지 않습니다.
MSG
