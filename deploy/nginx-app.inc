# Skiff 앱 라우팅 — HTTP(80) 와 HTTPS(443) 서버 블록이 함께 include 한다.
#
# 한 곳에만 두는 이유: 두 블록에 복사해두면 한쪽만 고치는 일이 반드시 생긴다.
# 특히 X-Accel-Redirect 의 internal 경로가 한쪽에만 있으면 그쪽에서만
# 다운로드가 죽는데, 원인을 찾기까지 오래 걸린다.

# ── 보안 헤더 ──
#
# always 를 붙여야 오류 응답(410, 429 등)에도 나간다. 붙이지 않으면 2xx/3xx
# 에만 적용되어, 정작 공격 시도가 몰리는 응답에서 빠진다.

# 브라우저가 Content-Type 을 무시하고 내용을 추측하지 못하게 한다.
# 파일 전송 서비스에서 특히 중요하다 — 업로드된 파일을 HTML 로 추측해 실행하면
# 우리 도메인에서 남의 스크립트가 도는 셈이 된다.
add_header X-Content-Type-Options "nosniff" always;

# 클릭재킹 방어. 관리자 화면에 삭제 버튼이 있어서, 투명 iframe 으로 덮어
# 관리자가 모르는 사이 누르게 만드는 공격이 가능하다.
add_header X-Frame-Options "DENY" always;

# **공유 코드가 URL 경로에 있다.** Referer 헤더로 외부에 새어 나가면
# 링크를 아는 사람이 늘어난다. 아예 보내지 않는다.
add_header Referrer-Policy "no-referrer" always;

# 정적 내보내기라 Next 가 하이드레이션 데이터를 인라인 스크립트로 넣는다.
# 그래서 'unsafe-inline' 이 불가피하다(nonce 는 SSR 이 있어야 쓸 수 있다).
# 그럼에도 값어치가 있다 — 외부 스크립트 주입과 외부로의 데이터 유출을 막고,
# frame-ancestors 로 클릭재킹을 한 번 더 차단한다.
add_header Content-Security-Policy "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'" always;

# ── 업로드 본문 크기 ──
# 파트 하나가 8MiB 다. 기본값(1m)이면 업로드가 413 으로 전부 실패한다.
client_max_body_size 16m;
# 본문을 임시 파일에 다 받아놓고 넘기지 않는다. 스트리밍으로 그대로 흘린다.
proxy_request_buffering off;
client_body_timeout 300s;

# ── API ──
location /api/ {
    proxy_pass http://skiff_api;
    proxy_http_version 1.1;

    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    # 앱은 이 헤더의 **마지막** 값을 클라이언트 IP 로 쓴다.
    # $proxy_add_x_forwarded_for 가 뒤에 $remote_addr 을 붙여주므로
    # 위조된 앞쪽 값이 있어도 마지막은 항상 진짜다.
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Connection        "";

    # CloudFront 뒤에 있을 때만 의미가 있는 헤더들이다. 여기는 CloudFront 가
    # 없으므로 클라이언트가 보낸 값이 그대로 앱에 도달하면 안 된다.
    # 빈 값으로 덮으면 nginx 가 해당 헤더를 오리진에 보내지 않는다.
    # (앱도 배포 형태를 보고 무시하지만, 두 겹으로 막는다.)
    proxy_set_header CloudFront-Viewer-Address "";
    proxy_set_header CloudFront-Forwarded-Proto "";
    proxy_set_header X-Real-IP-Forwarded "";

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
    alias /var/lib/skiff/files/;

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
    # `$uri.html` 이 반드시 있어야 한다. Next 정적 내보내기는 /admin 페이지를
    # `admin/index.html` 이 아니라 **`admin.html`** 로 만든다. 이 항목이 없으면
    # /admin 요청이 아래 404.html 폴백으로 떨어져, 관리자 페이지 대신
    # "페이지를 찾을 수 없습니다" 가 뜬다.
    #
    # 공유 링크(/oslo/113245)는 실제 파일이 아니다. 어느 것도 매칭되지 않으면
    # 404.html 을 200 으로 돌려주고, Next 앱 셸이 떠서 클라이언트가 경로를
    # 읽고 수신 화면을 그린다.
    try_files $uri $uri.html $uri/ /404.html;
}

error_page 404 =200 /404.html;
