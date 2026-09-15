# Dropship 앱 라우팅 — HTTP(80) 와 HTTPS(443) 서버 블록이 함께 include 한다.
#
# 한 곳에만 두는 이유: 두 블록에 복사해두면 한쪽만 고치는 일이 반드시 생긴다.
# 특히 X-Accel-Redirect 의 internal 경로가 한쪽에만 있으면 그쪽에서만
# 다운로드가 죽는데, 원인을 찾기까지 오래 걸린다.

# ── 업로드 본문 크기 ──
# 파트 하나가 8MiB 다. 기본값(1m)이면 업로드가 413 으로 전부 실패한다.
client_max_body_size 16m;
# 본문을 임시 파일에 다 받아놓고 넘기지 않는다. 스트리밍으로 그대로 흘린다.
proxy_request_buffering off;
client_body_timeout 300s;

# ── API ──
location /api/ {
    proxy_pass http://dropship_api;
    proxy_http_version 1.1;

    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    # 앱은 이 헤더의 **마지막** 값을 클라이언트 IP 로 쓴다.
    # $proxy_add_x_forwarded_for 가 뒤에 $remote_addr 을 붙여주므로
    # 위조된 앞쪽 값이 있어도 마지막은 항상 진짜다.
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Connection        "";

    proxy_read_timeout  300s;
    proxy_send_timeout  300s;
    proxy_buffering off;
}

# ── 업로드된 파일 (내부 전용) ──
#
# internal 이라 외부에서 직접 열 수 없다. FastAPI 가 토큰을 확인한 뒤
# X-Accel-Redirect: /protected/<key> 를 돌려주면 nginx 가 여기로 내부
# 전환해 sendfile 로 보낸다. Python 은 바이트를 하나도 만지지 않는다.
location /protected/ {
    internal;
    alias /var/lib/dropship/files/;

    sendfile on;
    tcp_nopush on;
    # 이미 압축된 파일이 대부분이고, 압축하면 CPU 만 쓴다.
    gzip off;
}

# ── 프론트엔드 정적 파일 ──
# 해시가 박힌 빌드 산출물은 내용이 바뀌면 이름도 바뀐다. 영구 캐시 가능.
location /_next/static/ {
    expires 1y;
    add_header Cache-Control "public, immutable";
}

location / {
    # 공유 링크(/oslo/113245)는 실제 파일이 아니다. 404 대신 404.html 을
    # 200 으로 돌려주면 Next 앱 셸이 떠서 클라이언트가 경로를 읽고 수신
    # 화면을 그린다.
    try_files $uri $uri/ /404.html;
}

error_page 404 =200 /404.html;
