# Skiff Web — Next.js 정적 산출물 + nginx
#
#   docker build -f docker/web.Dockerfile -t skiff-web .
#
# 2단계로 빌드한다. Node 는 빌드에만 쓰고 최종 이미지에는 남기지 않는다 —
# 런타임에 필요 없는 570MB 짜리 node_modules 를 이미지에 넣을 이유가 없다.

# ── 1단계: 프론트엔드 빌드 ──
FROM node:20-slim AS build

WORKDIR /src

# 락파일과 매니페스트를 먼저 복사해 의존성 레이어를 캐시한다.
COPY package.json package-lock.json ./
COPY apps/web/package.json ./apps/web/
RUN npm ci --no-fund --no-audit

COPY apps/web ./apps/web

# 운영에서는 nginx 가 프론트와 API 를 같은 도메인에 묶으므로 상대 경로로 부른다.
# 이 값을 비워두지 않으면 개발용 localhost:8000 이 번들에 박혀 배포본이 죽는다.
ENV NEXT_PUBLIC_API_BASE_URL="" \
    NEXT_TELEMETRY_DISABLED=1
RUN npm run build --workspace apps/web

# ── 2단계: nginx ──
FROM nginx:1.27-alpine

# API 컨테이너가 만든 0640 파일을 읽어야 한다(X-Accel-Redirect).
# 같은 GID 를 공유하지 않으면 다운로드가 전부 403 으로 죽는다.
RUN addgroup -g 10001 skiff && addgroup nginx skiff

COPY --from=build /src/apps/web/out /var/www/skiff
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
# 라우팅 규칙은 호스트 배포와 **같은 파일**을 쓴다. 복사해두면 한쪽만 고치는
# 일이 생기고, X-Accel-Redirect 경로가 어긋나면 그쪽에서만 다운로드가 죽는다.
COPY deploy/nginx-app.inc /etc/nginx/skiff-app/app.conf

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qO- http://localhost/api/health || exit 1
