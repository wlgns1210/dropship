"""관리자 지표 API.

보안 설계
---------
공개 익명 서비스에 붙는 화면이라 기본값이 **꺼짐**이다. ``ADMIN_TOKEN`` 이
비어 있으면 이 라우터 자체가 등록되지 않는다 — 실수로 열리는 경로를 없애려면
"인증 실패로 막는다" 보다 "존재하지 않는다" 가 낫다.

토큰 비교는 ``compare_digest`` 로 한다. 일반 비교는 앞에서부터 다르면 즉시
끝나서, 응답 시간 차이로 토큰을 한 바이트씩 맞춰갈 수 있다.

여기서 내보내는 것은 **집계값뿐이다.** 파일명·공유 코드·업로더 IP 는 나가지
않는다. 관리자 화면이 뚫렸을 때 피해를 "서버가 얼마나 바쁜지 알려짐" 으로
묶어두기 위해서다.
"""

import json
import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.config import DAILY_QUOTA_BYTES, MAX_TOTAL_BYTES, get_settings
from app.deps import IpHashDep, RepositoryDep, rate_limit
from app.services import system_stats

router = APIRouter(prefix="/api/admin", tags=["admin"])

#: 감시할 systemd 유닛
_UNITS = ("dropship-api.service", "nginx.service", "dropship-sweeper.timer")


def require_admin(
    repo: RepositoryDep,
    ip_hash: IpHashDep,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Bearer 토큰 확인.

    실패해도 레이트리밋을 소비시킨다. 안 그러면 토큰을 무제한으로 때려볼 수
    있다. 성공/실패 모두 같은 스코프를 쓰는 이유이기도 하다.
    """
    settings = get_settings()
    if not repo.allow_request(scope="admin", ip_hash=ip_hash, limit=20, window=300):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="요청이 너무 잦습니다.",
            headers={"Retry-After": "300"},
        )

    provided = ""
    if authorization and authorization.lower().startswith("bearer "):
        provided = authorization[7:].strip()

    if not secrets.compare_digest(provided, settings.admin_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="인증이 필요합니다.",
            headers={"WWW-Authenticate": "Bearer"},
        )


AdminOnly = Depends(require_admin)


@router.get("/stats", dependencies=[AdminOnly], summary="서버·서비스 지표")
async def stats(repo: RepositoryDep) -> dict[str, Any]:
    settings = get_settings()

    system = system_stats.collect_static()
    # CPU 는 두 시점을 재야 해서 다른 항목과 수집 방식이 다르다.
    system["cpu_percent"] = await system_stats.cpu_percent()

    files_dir = settings.files_dir
    payload: dict[str, Any] = {
        "system": system,
        "disk": system_stats.disk(files_dir if files_dir.exists() else "/"),
        "files": system_stats.directory_size(files_dir) if files_dir.exists() else None,
        "services": {unit: system_stats.service_state(unit) for unit in _UNITS},
        "policy": {
            "max_total_bytes": MAX_TOTAL_BYTES,
            "daily_quota_bytes": DAILY_QUOTA_BYTES,
        },
        "deploy_mode": settings.deploy_mode,
    }

    # 앱 지표는 SQLite 구현에만 있다. AWS 모드에서는 CloudWatch 가 대신하므로
    # 없으면 그 칸만 비운다 — 화면 전체가 죽는 것보다 낫다.
    app_stats = getattr(repo, "stats", None)
    if app_stats is not None:
        collected = app_stats()
        raw_sweep = collected.pop("last_sweep", None)
        if raw_sweep:
            try:
                collected["last_sweep"] = json.loads(raw_sweep)
            except (ValueError, TypeError):
                collected["last_sweep"] = None
        else:
            collected["last_sweep"] = None
        payload |= collected

    return payload


@router.get("/ping", dependencies=[rate_limit("lookup")], summary="관리자 기능 활성 여부")
def ping() -> dict[str, bool]:
    """토큰 없이도 부를 수 있다.

    관리자 페이지가 "토큰을 넣어라" 와 "이 서버는 관리자 기능이 꺼져 있다" 를
    구분해 안내하기 위한 것이고, 노출되는 정보는 이 라우터의 존재 여부뿐이다.
    라우터가 등록되지 않았다면 404 가 나므로 그것으로도 판별된다.
    """
    return {"enabled": True}
