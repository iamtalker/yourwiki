"""P2P 창구를 Cloudflare 없이 직접 여는 방법 (유어위키 1.2, 표준 라이브러리만 사용).

1) 공유기 포트 자동 열기(UPnP IGD): 집 공유기에 '바깥 포트 → 이 컴퓨터 3002번' 을 잠깐(1시간) 빌려 달라고 한다.
   공유기 설정 화면에 들어갈 필요가 없다. 공유기가 UPnP 를 끄고 있거나, 통신사가 공인 IP 를 여러 집에
   나눠 쓰게 하면(CGNAT) 안 된다.
2) IPv6: 이 컴퓨터에 공인 IPv6 주소가 있으면 그 주소로 바로 받는다. 다만 공유기 방화벽이 바깥에서 들어오는
   IPv6 연결을 막는 경우가 많아, 안 되면 다른 위키들이 두 번째 주소(보통 Cloudflare)로 넘어간다.

직접 연결은 내 공인 IP 가 다른 참여자에게 보인다(BitTorrent 와 같다). 그래서 관리판에서 고를 때만 쓴다.

    python direct.py          # 이 컴퓨터에서 직접 연결이 되는지 알아보기(포트를 1분만 열었다 닫는다)
"""
import ipaddress
import os
import re
import socket
import time
import urllib.parse
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

SSDP = ("239.255.255.250", 1900)
SEARCH = ("urn:schemas-upnp-org:device:InternetGatewayDevice:1", "urn:schemas-upnp-org:device:InternetGatewayDevice:2",
          "urn:schemas-upnp-org:service:WANIPConnection:1", "urn:schemas-upnp-org:service:WANIPConnection:2",
          "urn:schemas-upnp-org:service:WANPPPConnection:1")
SERVICES = ("WANIPConnection", "WANPPPConnection")
LEASE = 3600
DESC = "YourWiki P2P"


class DirectError(Exception):
    pass


# ---------------------------------------------------------------- UPnP
def discover(timeout=2.5):
    """공유기의 UPnP 설명 주소(LOCATION)들. 시험용으로 YOURWIKI_UPNP_LOCATION 을 주면 그것만 쓴다."""
    env = os.environ.get("YOURWIKI_UPNP_LOCATION")
    if env:
        return [env]
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    s.settimeout(0.3)
    try:
        for st in SEARCH:
            msg = (f'M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: "ssdp:discover"\r\nMX: 2\r\n'
                   f"ST: {st}\r\n\r\n").encode()
            try:
                s.sendto(msg, SSDP)
            except OSError:
                return []
        out, end = [], time.time() + timeout
        while time.time() < end:
            try:
                data, frm = s.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            m = re.search(rb"(?im)^location:\s*(\S+)", data)
            if not m:
                continue
            loc = m.group(1).decode("latin-1")
            host = urllib.parse.urlsplit(loc).hostname or ""
            # 답한 기기 자신의 내부 주소만 믿는다(엉뚱한 곳으로 요청을 보내게 하지 않도록)
            if host == frm[0] and ipaddress.ip_address(host).is_private and loc not in out:
                out.append(loc)
        return out
    finally:
        s.close()


def _strip(tag):
    return tag.rsplit("}", 1)[-1]


def control_url(location):
    """설명 XML 에서 WANIPConnection(또는 WANPPPConnection) 서비스의 (controlURL, serviceType)."""
    with urllib.request.urlopen(location, timeout=5) as r:
        root = ET.fromstring(r.read(1 << 20))
    base = location
    for el in root.iter():
        if _strip(el.tag) == "URLBase" and (el.text or "").strip():
            base = el.text.strip()
    for svc in root.iter():
        if _strip(svc.tag) != "service":
            continue
        f = {_strip(c.tag): (c.text or "").strip() for c in svc}
        if any(x in f.get("serviceType", "") for x in SERVICES) and f.get("controlURL"):
            return urllib.parse.urljoin(base, f["controlURL"]), f["serviceType"]
    raise DirectError("공유기가 포트 열기(WANIPConnection)를 알려 주지 않습니다")


def soap(ctrl, stype, action, args=()):
    body = "".join(f"<{k}>{v}</{k}>" for k, v in args)
    xml = ('<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
           's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
           f'<u:{action} xmlns:u="{stype}">{body}</u:{action}></s:Body></s:Envelope>').encode()
    req = urllib.request.Request(ctrl, data=xml, headers={
        "Content-Type": 'text/xml; charset="utf-8"', "SOAPAction": f'"{stype}#{action}"'})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            data = r.read(1 << 20)
    except urllib.error.HTTPError as e:
        data = e.read(1 << 16)
        code = re.search(rb"<(?:\w+:)?errorCode>(\d+)<", data)
        raise DirectError(f"공유기가 거절함({action}, 오류 {code.group(1).decode() if code else e.code})")
    out = {}
    for el in ET.fromstring(data).iter():
        if not list(el):
            out[_strip(el.tag)] = (el.text or "").strip()
    return out


def local_ip_to(host):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((host, 1900))  # 패킷은 보내지 않는다. 공유기 쪽으로 나가는 내 내부 주소만 알아낸다
        return s.getsockname()[0]
    finally:
        s.close()


class UPnP:
    def __init__(self):
        self.ctrl = self.stype = None
        self.mapped = None  # (바깥 포트)

    def gateway(self):
        if self.ctrl:
            return
        errs = []
        for loc in discover():
            try:
                self.ctrl, self.stype = control_url(loc)
                return
            except (OSError, ValueError, ET.ParseError, DirectError) as e:
                errs.append(str(e))
        raise DirectError("공유기에서 UPnP 답이 없습니다(공유기 설정에서 UPnP 가 꺼져 있거나 지원하지 않음)"
                          + (f": {errs[0]}" if errs else ""))

    def open(self, internal_port, external_port):
        """포트를 열고 (공인 IPv4, 바깥 포트). 이미 다른 기기가 쓰는 포트면 다음 번호를 시도한다."""
        self.gateway()
        me = local_ip_to(urllib.parse.urlsplit(self.ctrl).hostname)
        ext = soap(self.ctrl, self.stype, "GetExternalIPAddress").get("NewExternalIPAddress", "")
        try:
            ok = ipaddress.ip_address(ext).is_global
        except ValueError:
            ok = False
        if not ok and not os.environ.get("YOURWIKI_P2P_ALLOW_LOCAL"):
            raise DirectError(f"공유기의 바깥 주소({ext or '없음'})가 공인 IP 가 아닙니다"
                              "(통신사 공유 IP·이중 공유기). 직접 연결은 IPv6 나 Cloudflare 로만 됩니다")
        last = None
        for port in [external_port] + [external_port + i * 7 for i in range(1, 5)]:
            port = 1024 + (port - 1024) % 64000
            for lease in (LEASE, 0):  # 일부 공유기는 '영구'(0)만 받는다
                try:
                    soap(self.ctrl, self.stype, "AddPortMapping", [
                        ("NewRemoteHost", ""), ("NewExternalPort", port), ("NewProtocol", "TCP"),
                        ("NewInternalPort", internal_port), ("NewInternalClient", me), ("NewEnabled", 1),
                        ("NewPortMappingDescription", DESC), ("NewLeaseDuration", lease)])
                    self.mapped = port
                    return ext, port
                except DirectError as e:
                    last = e
                    if "725" not in str(e):  # 725 = 영구만 됨 → lease 0 으로 다시. 그 밖(718 충돌 등) → 다음 포트
                        break
        raise last or DirectError("포트를 열지 못했습니다")

    def close(self):
        if self.ctrl and self.mapped:
            try:
                soap(self.ctrl, self.stype, "DeletePortMapping",
                     [("NewRemoteHost", ""), ("NewExternalPort", self.mapped), ("NewProtocol", "TCP")])
            except (OSError, DirectError):
                pass
            self.mapped = None


# ---------------------------------------------------------------- 공인 IP 가 바로 붙은 컴퓨터(서버)
def public_ipv4():
    """이 컴퓨터에 공인 IPv4 가 바로 붙어 있으면(공유기 없는 서버) 그 주소, 아니면 ""."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # 패킷은 보내지 않는다
        a = s.getsockname()[0]
        return a if ipaddress.ip_address(a).is_global else ""
    except OSError:
        return ""
    finally:
        s.close()


# ---------------------------------------------------------------- IPv6
def ipv6_address():
    """이 컴퓨터가 인터넷으로 나갈 때 쓰는 공인 IPv6 주소(없으면 "")."""
    try:
        s = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
    except OSError:
        return ""
    try:
        s.connect(("2001:4860:4860::8888", 80))  # 패킷은 보내지 않는다. 나가는 주소만 알아낸다
        a = s.getsockname()[0].split("%")[0]
        return a if ipaddress.ip_address(a).is_global else ""
    except OSError:
        return ""
    finally:
        s.close()


def port_for(my_id):
    """바깥 포트: 위키마다 다르게(한 집에 유어위키가 둘이어도 부딪히지 않게), 같은 위키는 늘 같게."""
    return 20000 + int(my_id[:8] or "0", 16) % 40000


class Direct:
    """p2p.py 가 20분마다 불러 포트를 다시 빌리고(1시간짜리), 지금 쓸 수 있는 직접 주소들을 돌려받는다."""
    EVERY = 1200

    def __init__(self, window_port, my_id, log=print):
        self.window_port, self.ext_port, self.log = window_port, port_for(my_id), log
        self.upnp = UPnP()
        self.urls, self.at, self.said = [], 0.0, {}

    def _say(self, key, msg):
        if self.said.get(key) != msg:  # 같은 말을 20분마다 되풀이하지 않는다
            self.said[key] = msg
            self.log(msg)

    def refresh(self, force=False):
        if not force and time.time() - self.at < self.EVERY:
            return self.urls
        self.at, urls = time.time(), []
        v4 = public_ipv4()
        if v4:
            urls.append(f"http://{v4}:{self.window_port}")
            self._say("v4", f"이 컴퓨터에 공인 IP 가 있어 바로 받습니다: http://{v4}:{self.window_port}"
                            f"(방화벽에서 {self.window_port}/tcp 를 열어 두세요)")
        try:
            if v4:
                raise DirectError("skip")
            ip, port = self.upnp.open(self.window_port, self.ext_port)
            urls.append(f"http://{ip}:{port}")
            self._say("v4", f"공유기 포트를 자동으로 열었습니다(UPnP): http://{ip}:{port}")
        except (OSError, ValueError, ET.ParseError, DirectError) as e:
            if not v4:
                self._say("v4", f"공유기 포트 자동 열기(UPnP) 안 됨: {e}")
        v6 = ipv6_address()
        if v6:
            urls.append(f"http://[{v6}]:{self.window_port}")
            self._say("v6", f"공인 IPv6 주소로도 받습니다: http://[{v6}]:{self.window_port}"
                            "(공유기 방화벽이 막으면 다른 위키는 두 번째 주소로 넘어갑니다)")
        else:
            self._say("v6", "공인 IPv6 주소가 없습니다")
        self.urls = urls
        return urls

    def close(self):
        self.upnp.close()


if __name__ == "__main__":
    d = Direct(3002, "0" * 64)
    print("직접 주소:", d.refresh() or "없음")
    time.sleep(60)
    d.close()
