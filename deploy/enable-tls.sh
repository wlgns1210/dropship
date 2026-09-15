#!/usr/bin/env bash
# Dropship — TLS 켜기 (Let's Encrypt)
#
#   sudo bash deploy/enable-tls.sh <호스트명> <이메일>
#
# 도메인을 사지 않고도 진짜 인증서를 받을 수 있다. sslip.io 는 호스트명에 든
# IP 를 그대로 돌려주는 와일드카드 DNS 라, 15.164.209.152.sslip.io 가 곧
# 그 서버를 가리킨다. Let's Encrypt 는 이 호스트명에 인증서를 발급해 준다
# (IP 주소 자체에는 발급하지 않는다).
#
# 여러 번 돌려도 안전하다. 인증서가 이미 있으면 재사용한다.
set -euo pipefail

HOST="${1:?사용법: enable-tls.sh <호스트명> <이메일>}"
EMAIL="${2:?사용법: enable-tls.sh <호스트명> <이메일>}"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

say "사전 확인"
RESOLVED=$(getent hosts "$HOST" | awk '{print $1}' | head -1)
[ -n "$RESOLVED" ] || { echo "DNS 해석 실패: $HOST" >&2; exit 1; }
echo "  $HOST -> $RESOLVED"

command -v certbot >/dev/null || dnf install -y -q certbot python3-certbot-nginx

mkdir -p /var/www/certbot /etc/nginx/dropship-app /etc/nginx/dropship-http
# 챌린지를 받으려면 80 블록이 앱을 서비스하고 있어야 한다. 아직 리다이렉트를
# 걸지 않은 상태에서 먼저 인증서를 받는다 — 순서를 바꾸면 리다이렉트된 HTTPS
# 쪽에 인증서가 없어 챌린지 자체가 실패한다.
nginx -t && systemctl reload nginx

say "인증서 발급"
if [ -d "/etc/letsencrypt/live/$HOST" ]; then
    echo "  기존 인증서 재사용"
else
    # nginx 플러그인 대신 webroot 를 쓴다. 플러그인은 우리 설정을 자동으로
    # 고쳐놓는데, include 로 쪼개둔 구조를 예상대로 다루지 못한다.
    certbot certonly --webroot -w /var/www/certbot \
        -d "$HOST" \
        --email "$EMAIL" \
        --agree-tos --no-eff-email --non-interactive
fi

say "HTTPS 서버 블록 설치"
cat > /etc/nginx/conf.d/dropship-tls.conf <<EOF
server {
    listen 443 ssl default_server;
    listen [::]:443 ssl default_server;
    http2 on;
    server_name $HOST;

    server_tokens off;

    ssl_certificate     /etc/letsencrypt/live/$HOST/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$HOST/privkey.pem;

    # TLS 1.2 미만은 받지 않는다. 1.0/1.1 은 2021년에 폐기됐다.
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 1d;
    ssl_session_tickets off;

    # 브라우저에게 "이 사이트는 앞으로 HTTPS 로만 접속하라"고 알린다.
    # 한 번 받은 브라우저는 평문으로 요청조차 하지 않는다.
    add_header Strict-Transport-Security "max-age=31536000" always;

    access_log /var/log/nginx/dropship.access.log;
    error_log  /var/log/nginx/dropship.error.log;

    root /var/www/dropship;
    index index.html;

    include /etc/nginx/dropship-app/*.conf;

    gzip on;
    gzip_types text/css application/javascript application/json image/svg+xml;
    gzip_min_length 1024;
}
EOF

say "HTTP 를 HTTPS 로 넘기기"
# 80 번 블록이 읽는 디렉터리에서 앱 라우팅을 빼고 리다이렉트만 남긴다.
# 둘 다 두면 `location /` 이 중복되어 nginx 가 기동하지 못한다.
rm -f /etc/nginx/dropship-http/app.conf
cat > /etc/nginx/dropship-http/redirect.conf <<EOF
# ACME 챌린지 location 은 이 include 보다 앞에 있어 그대로 동작한다.
# 인증서 갱신이 리다이렉트에 막히지 않는다.
location / {
    return 308 https://$HOST\$request_uri;
}
EOF

nginx -t
systemctl reload nginx

say "자동 갱신 확인"
# certbot 패키지가 systemd 타이머를 함께 설치한다. 하루 두 번 깨어나
# 만료 30일 전부터 갱신을 시도한다.
systemctl enable --now certbot-renew.timer 2>/dev/null || true
systemctl list-timers certbot-renew.timer --no-pager | tail -2
certbot renew --dry-run 2>&1 | tail -3

say "완료"
echo "  https://$HOST"
