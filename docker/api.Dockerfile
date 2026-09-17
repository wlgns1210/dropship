# Skiff API — FastAPI + uvicorn
#
#   docker build -f docker/api.Dockerfile -t skiff-api .
#
# 빌드 컨텍스트는 저장소 루트다.

FROM python:3.12-slim

# .pyc 를 남기지 않고 출력을 버퍼링하지 않는다. 버퍼링을 끄지 않으면
# 컨테이너 로그가 뭉쳐서 늦게 나와 장애를 볼 때 불리하다.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# ── 의존성 ──
#
# pyproject 만 먼저 복사해 의존성 목록을 뽑아 설치한다. 앱 코드가 바뀌어도
# 이 레이어는 캐시되어 재빌드가 몇 초로 끝난다. 코드를 먼저 복사하면
# 한 글자만 고쳐도 매번 전체를 다시 설치한다.
#
# 패키지 자체(`pip install .`)는 설치하지 않는다. uvicorn 을 /app 에서
# 실행하므로 app/ 디렉터리가 그 자리에 있으면 임포트된다.
COPY apps/api/pyproject.toml ./
RUN python - <<'PY' > /tmp/requirements.txt \
 && pip install --no-cache-dir -r /tmp/requirements.txt \
 && rm /tmp/requirements.txt
import tomllib
with open("pyproject.toml", "rb") as handle:
    data = tomllib.load(handle)
# 기본 의존성 + [server](uvicorn). dev 는 넣지 않는다 — 테스트 도구가
# 운영 이미지에 있을 이유가 없고 크기만 키운다.
for spec in data["project"]["dependencies"]:
    print(spec)
for spec in data["project"]["optional-dependencies"]["server"]:
    print(spec)
PY

# ── 앱 ──
COPY apps/api/app ./app

# 정리 루프를 이미지 안에 넣는다. 배포 VM 은 저장소를 받지 않고 이미지만
# 가져오므로, 호스트 파일을 마운트하는 방식이면 sweeper 가 뜨지 않는다.
COPY docker/sweeper.sh /usr/local/bin/skiff-sweeper
RUN chmod +x /usr/local/bin/skiff-sweeper

# ── 실행 사용자 ──
# 익명 업로드를 받는 프로세스라 루트로 돌리지 않는다.
#
# UID/GID 를 고정하는 것이 중요하다. nginx 컨테이너가 같은 볼륨의 파일을
# 직접 읽어야 하는데(X-Accel-Redirect), 파일이 0640 이라 같은 그룹이어야
# 읽힌다. 두 이미지에서 GID 10001 을 맞춘다.
RUN groupadd -g 10001 skiff \
 && useradd -u 10001 -g 10001 -M -s /usr/sbin/nologin skiff \
 && mkdir -p /var/lib/skiff/files \
 && chown -R skiff:skiff /var/lib/skiff \
 && chmod 750 /var/lib/skiff /var/lib/skiff/files

USER skiff

ENV SKIFF_ENV=production \
    DEPLOY_MODE=single \
    DATA_DIR=/var/lib/skiff

EXPOSE 8000

# umask 0027 로 앱이 만드는 파일을 640, 디렉터리를 750 으로 고정한다.
# 기본값(022)이면 업로드 파일과 SQLite DB 가 누구나 읽을 수 있는 644 가 된다.
CMD ["sh", "-c", "umask 0027 && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2 --proxy-headers --forwarded-allow-ips='*' --log-level info"]
