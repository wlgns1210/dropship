"""FastAPI 앱 진입점. 로컬에서는 uvicorn, 운영에서는 Lambda(Mangum)로 뜬다."""

import secrets
from typing import Any

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware

from app.config import (
    DAILY_QUOTA_BYTES,
    EXPIRY_CHOICES,
    MAX_FILES,
    MAX_TOTAL_BYTES,
    ORIGIN_SECRET_HEADER,
    get_settings,
)
from app.routers import transfers

settings = get_settings()

app = FastAPI(
    title="Skiff API",
    description="익명 파일 전송 — 링크와 QR로 보내고, 정해진 기간이 지나면 사라진다",
    version="0.1.0",
    # 운영에서는 자동 문서를 끈다. 관리자 엔드포인트를 포함한 전체 API 구조와
    # 스키마가 그대로 노출되기 때문이다.
    #
    # 지금은 nginx 가 /api/* 만 프록시해서 /docs 가 어차피 닿지 않지만, 그건
    # 라우팅 설정에 기댄 우연이다. 나중에 프록시 범위가 넓어지면 조용히 열린다.
    docs_url="/docs" if settings.is_local else None,
    redoc_url="/redoc" if settings.is_local else None,
    openapi_url="/openapi.json" if settings.is_local else None,
)

# 운영에서는 CloudFront 가 프론트와 API 를 같은 도메인으로 묶으므로 CORS 가 필요 없다.
# 로컬에서만 Next.js(3000) → FastAPI(8000) 교차 출처가 생긴다.
if settings.is_local:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

if settings.origin_secret:

    @app.middleware("http")
    async def require_cloudfront_origin(request: Request, call_next):  # type: ignore[no-untyped-def]
        """CloudFront 를 거치지 않은 요청을 막는다.

        Lambda 함수 URL 은 공개 주소다. 이 검사가 없으면 주소를 알아낸 사람이
        CloudFront 를 건너뛰고 직접 호출할 수 있고, 그 경우 ``X-Forwarded-For`` 를
        매 요청 다르게 꾸며 레이트리밋과 일일 쿼터를 무제한 우회할 수 있다.

        비교는 ``compare_digest`` 로 한다. 문자열 비교의 조기 종료 시간차로
        시크릿을 한 바이트씩 알아내는 것을 막기 위해서다.
        """
        supplied = request.headers.get(ORIGIN_SECRET_HEADER, "")
        if not secrets.compare_digest(supplied, settings.origin_secret):
            return Response(status_code=status.HTTP_403_FORBIDDEN)
        return await call_next(request)


app.include_router(transfers.router)
app.include_router(transfers.zip_router)

if settings.is_single_node:
    # 파트 업로드 수신과 다운로드 인가. AWS 모드에서는 S3 가 presigned URL 로
    # 직접 처리하므로 등록하지 않는다.
    from app.routers import local_transfer

    app.include_router(local_transfer.router)

if settings.admin_token:
    # 토큰이 없으면 라우터가 존재하지 않는다. 설정 실수로 관리자 화면이
    # 열려버리는 경로를 원천적으로 없앤다.
    from app.routers import admin

    app.include_router(admin.router)


@app.get("/api/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok", "env": settings.skiff_env, "mode": settings.deploy_mode}


@app.get("/api/config", tags=["meta"])
def client_config() -> dict[str, Any]:
    """프론트가 하드코딩 없이 정책 값을 가져가는 곳.

    상한이나 보관 기간 선택지를 바꿀 때 프론트를 같이 고칠 필요가 없다.
    """
    return {
        "max_total_bytes": MAX_TOTAL_BYTES,
        "max_files": MAX_FILES,
        "expiry_choices": list(EXPIRY_CHOICES),
        "daily_quota_bytes": DAILY_QUOTA_BYTES,
    }


# Lambda 핸들러. 로컬 실행에는 쓰이지 않는다.
def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    from mangum import Mangum

    return Mangum(app, lifespan="off")(event, context)
