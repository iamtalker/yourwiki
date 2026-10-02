# 유어위키 Docker 이미지 (데이터 5GB 는 이미지에 넣지 않고, 처음 켤 때 받아서 볼륨에 저장)
FROM python:3.12-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends p7zip-full curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /kit
COPY . /kit
ENV LISTEN=0.0.0.0:3000 SYNC=auto COLOR=#3b5bdb EDITION= PYTHONUTF8=1 P2P=off P2P_HUBS= P2P_FRIENDS= P2P_URL= P2P_OPEN=tunnel HUB=off
EXPOSE 3000
VOLUME ["/kit/wiki", "/kit/data"]
ENTRYPOINT ["bash", "docker/entrypoint.sh"]
