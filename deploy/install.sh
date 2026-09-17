#!/usr/bin/env bash
# Skiff 단일 EC2 배포 스크립트 (Amazon Linux 2023)
#
#   sudo bash deploy/install.sh
#
# 여러 번 돌려도 안전하다. 코드만 바뀌었으면 그냥 다시 돌리면 된다.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR=/opt/skiff
DATA_DIR=/var/lib/skiff
WEB_DIR=/var/www/skiff
ENV_FILE=/etc/skiff/skiff.env

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

# ── 사용자 ────────────────────────────────────────────────────
# 서비스 전용 계정. 로그인 불가, 홈 없음. 익명 업로드를 받는 프로세스가
# ec2-user 권한으로 돌면 뚫렸을 때 SSH 키까지 읽힌다.
if ! id skiff &>/dev/null; then
    say "skiff 사용자 생성"
    useradd --system --no-create-home --shell /sbin/nologin skiff
fi

# ── 패키지 ────────────────────────────────────────────────────
say "패키지 확인"
dnf install -y -q nginx python3.12 python3.12-pip >/dev/null

# ── 디렉터리 ──────────────────────────────────────────────────
say "디렉터리 준비"
mkdir -p "$APP_DIR" "$DATA_DIR/files" "$WEB_DIR" "$(dirname "$ENV_FILE")"
chown -R skiff:skiff "$DATA_DIR"
usermod -a -G skiff nginx

# nginx 는 skiff 그룹에 속해 X-Accel-Redirect 로 파일을 읽는다.
# 그 외 사용자에게는 아무 권한도 주지 않는다 — 업로드 파일과 SQLite DB 는
# 남의 파일명과 owner_token 을 담고 있다.
chmod 750 "$DATA_DIR" "$DATA_DIR/files"
# 이전 버전이 만든 644 파일들을 바로잡는다. 여러 번 돌려도 무해하다.
find "$DATA_DIR" -type d -exec chmod 750 {} +
find "$DATA_DIR" -type f -exec chmod 640 {} +

# ── 백엔드 ────────────────────────────────────────────────────
say "백엔드 코드 배치"
rm -rf "$APP_DIR/api"
mkdir -p "$APP_DIR/api"
cp -r "$REPO_DIR/apps/api/app" "$APP_DIR/api/"
cp "$REPO_DIR/apps/api/pyproject.toml" "$APP_DIR/api/"

if [ ! -x "$APP_DIR/venv/bin/python" ]; then
    say "가상환경 생성"
    python3.12 -m venv "$APP_DIR/venv"
fi
say "의존성 설치"
"$APP_DIR/venv/bin/pip" install -q --upgrade pip
# [server] 가 uvicorn 을 가져온다. Lambda 에서는 필요 없어서 기본 의존성이
# 아니지만, 상주 서버로 돌리려면 반드시 있어야 한다.
"$APP_DIR/venv/bin/pip" install -q "$APP_DIR/api[server]"
test -x "$APP_DIR/venv/bin/uvicorn" || { echo "uvicorn 설치 실패" >&2; exit 1; }
chown -R root:root "$APP_DIR"

# ── 프론트엔드 ────────────────────────────────────────────────
if [ -d "$REPO_DIR/apps/web/out" ]; then
    say "프론트엔드 배치"
    rm -rf "${WEB_DIR:?}"/*
    cp -r "$REPO_DIR/apps/web/out/." "$WEB_DIR/"
    chown -R root:nginx "$WEB_DIR"
    chmod -R a+rX "$WEB_DIR"
else
    echo "경고: apps/web/out 이 없습니다. 먼저 프론트엔드를 빌드하세요." >&2
fi

# ── 환경 파일 ─────────────────────────────────────────────────
if [ ! -f "$ENV_FILE" ]; then
    say "환경 파일 생성"
    SALT=$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')
    KEY=$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')
    ADMIN=$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')
    cat > "$ENV_FILE" <<EOF
# Skiff 단일 EC2 설정. 이 파일은 저장소에 두지 않는다.
SKIFF_ENV=production
DEPLOY_MODE=single
DATA_DIR=$DATA_DIR

# IP 해시 솔트 — 바꾸면 진행 중인 일일 쿼터 집계가 초기화된다.
IP_HASH_SALT=$SALT
# 업로드/다운로드 토큰 서명 키 — 바꾸면 발급된 링크가 즉시 무효가 된다.
URL_SIGNING_KEY=$KEY

# 관리자 페이지(/admin) 토큰. 비우면 관리자 API 가 아예 등록되지 않는다.
ADMIN_TOKEN=$ADMIN

# 단일 노드에서는 CloudFront 가 없으므로 오리진 시크릿 검사를 쓰지 않는다.
# nginx 가 유닉스 소켓으로만 붙고 API 는 포트를 열지 않아, 외부에서
# nginx 를 건너뛸 방법 자체가 없다.
ORIGIN_SECRET=
EOF
    chmod 640 "$ENV_FILE"
    chown root:skiff "$ENV_FILE"
    ADMIN_CREATED=1
else
    say "환경 파일 유지 (기존 키 보존)"
fi

# ── systemd ───────────────────────────────────────────────────
say "systemd 유닛 설치"
cp "$REPO_DIR/deploy/skiff-api.service" /etc/systemd/system/
cp "$REPO_DIR/deploy/skiff-sweeper.service" /etc/systemd/system/
cp "$REPO_DIR/deploy/skiff-sweeper.timer" /etc/systemd/system/
systemctl daemon-reload

# ── nginx ─────────────────────────────────────────────────────
say "nginx 설정"
# 앱 라우팅은 include 로 한 곳에만 둔다. HTTP(80)와 HTTPS(443) 블록이 같은
# 파일을 읽으므로, 한쪽만 고쳐서 생기는 불일치가 없다.
mkdir -p /etc/nginx/skiff-app /etc/nginx/skiff-http /var/www/certbot
cp "$REPO_DIR/deploy/nginx-app.inc" /etc/nginx/skiff-app/app.conf
cp "$REPO_DIR/deploy/nginx.conf" /etc/nginx/conf.d/skiff.conf

# 80 번 블록: TLS 가 이미 켜져 있으면 리다이렉트를 유지하고, 아니면 앱을
# 직접 서비스한다. 이 판단이 없으면 재설치할 때마다 HTTPS 리다이렉트가
# 지워져 평문으로 되돌아간다.
if [ -f /etc/nginx/skiff-http/redirect.conf ]; then
    echo "  TLS 리다이렉트 유지"
else
    cp "$REPO_DIR/deploy/nginx-app.inc" /etc/nginx/skiff-http/app.conf
fi

# AL2023 기본 설정에도 80 번을 듣는 server 블록이 있다. 그대로 두면
# "conflicting server name" 경고가 뜨고, 우리 블록이 default_server 라 동작은
# 하지만 어느 쪽이 응답하는지가 설정 읽는 순서에 의존하게 된다.
# 기본 블록을 루프백 8080 으로 옮겨 충돌 자체를 없앤다.
if grep -qE '^\s*listen\s+80;' /etc/nginx/nginx.conf 2>/dev/null; then
    sed -i -E 's/^(\s*)listen(\s+)80;/\1listen\2127.0.0.1:8080;/; s/^(\s*)listen(\s+)\[::\]:80;/\1listen\2[::1]:8080;/' /etc/nginx/nginx.conf
fi
nginx -t

# ── 기동 ──────────────────────────────────────────────────────

# TLS 가 켜져 있는데 443 블록이 없으면 사이트가 통째로 죽는다. 80 블록은
# HTTPS 로 리다이렉트하는데 그 HTTPS 에 받아줄 서버 블록이 없기 때문이다.
# 프로젝트 이름을 바꾸며 실제로 겪었다 — 서비스는 "정상"으로 뜨는데 아무것도
# 응답하지 않아서 원인을 찾는 데 시간이 걸렸다. 즉시 알아채도록 경고한다.
if [ -f /etc/nginx/skiff-http/redirect.conf ] && [ ! -f /etc/nginx/conf.d/skiff-tls.conf ]; then
    echo "" >&2
    echo "  경고: HTTPS 리다이렉트는 있는데 443 서버 블록이 없습니다." >&2
    echo "        deploy/enable-tls.sh <호스트명> <이메일> 을 다시 실행하세요." >&2
    echo "" >&2
fi

say "서비스 기동"
systemctl enable --now skiff-api.service
systemctl restart skiff-api.service
systemctl enable --now skiff-sweeper.timer
systemctl enable --now nginx
systemctl reload nginx

sleep 2
say "상태"
systemctl is-active skiff-api.service && echo "  API: 정상"
systemctl is-active nginx && echo "  nginx: 정상"
systemctl list-timers skiff-sweeper.timer --no-pager | tail -2

say "완료"
HOST_IP=$(curl -s --max-time 5 http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || echo '<서버-IP>')
echo "  http://$HOST_IP"

# 기존 환경 파일에 ADMIN_TOKEN 이 없으면(이전 버전에서 올라온 경우) 채워 준다.
if ! grep -q '^ADMIN_TOKEN=' "$ENV_FILE"; then
    ADMIN=$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')
    printf '\n# 관리자 페이지(/admin) 토큰\nADMIN_TOKEN=%s\n' "$ADMIN" >> "$ENV_FILE"
    systemctl restart skiff-api.service
    ADMIN_CREATED=1
fi

if [ "${ADMIN_CREATED:-0}" = "1" ]; then
    echo ""
    echo "  관리자 페이지: /admin"
    echo "  토큰: $(grep '^ADMIN_TOKEN=' "$ENV_FILE" | cut -d= -f2-)"
    echo "  (이 값은 $ENV_FILE 에 있습니다. 화면에서 한 번 입력하면 브라우저에 저장됩니다.)"
fi
