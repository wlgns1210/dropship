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

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, status

from app.config import (
    DAILY_QUOTA_BYTES,
    DISK_HEADROOM_BYTES,
    MAX_TOTAL_BYTES,
    get_settings,
)
from app.deps import IpHashDep, RepositoryDep, StorageDep, rate_limit
from app.services import codes, system_stats

router = APIRouter(prefix="/api/admin", tags=["admin"])

#: 감시할 systemd 유닛
_UNITS = ("skiff-api.service", "nginx.service", "skiff-sweeper.timer")


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
    disk = system_stats.disk(files_dir if files_dir.exists() else "/")

    # 사용률(%)만으로는 "지금 업로드를 받을 수 있는가" 에 답할 수 없다.
    # 16GB 디스크의 80% 와 1TB 디스크의 80% 는 남은 양이 전혀 다르다.
    # 서버가 실제로 쓰는 판단 기준을 그대로 노출한다.
    accepting = None
    if disk is not None:
        accepting = disk["free"] - MAX_TOTAL_BYTES >= DISK_HEADROOM_BYTES

    payload: dict[str, Any] = {
        "system": system,
        "disk": disk,
        "accepting_uploads": accepting,
        "disk_headroom_bytes": DISK_HEADROOM_BYTES,
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


# ── 전송 목록 · 삭제 ──────────────────────────────────────────
#
# 위의 /stats 와 달리 여기서는 **파일명과 공유 코드가 나간다.** 신고를 받았을 때
# 어떤 전송인지 찾아 지우려면 피할 수 없다. 대신 경로를 분리해 두어, 지표만
# 필요한 경우에 민감 정보가 따라 나가지 않게 했다.
#
# 업로더는 IP 해시의 앞 8자만 보여준다. 같은 사람이 반복해서 올리는지는
# 판단할 수 있으면서 원본 IP 는 복원되지 않는다.


@router.get("/transfers", dependencies=[AdminOnly], summary="업로드된 전송 목록")
def list_transfers(
    repo: RepositoryDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
) -> dict[str, Any]:
    lister = getattr(repo, "list_transfers", None)
    if lister is None:
        # AWS 모드에서는 전체 목록을 뽑으려면 테이블 스캔이 필요해 제공하지 않는다.
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="이 배포 형태에서는 목록 조회를 지원하지 않습니다.",
        )

    if status_filter not in (None, "ready", "pending"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="status 값이 올바르지 않습니다."
        )

    result: dict[str, Any] = lister(limit=limit, offset=offset, status=status_filter)
    return result


@router.delete(
    "/transfers/{word}/{number}", dependencies=[AdminOnly], summary="전송 강제 삭제"
)
def force_delete(
    repo: RepositoryDep,
    storage: StorageDep,
    word: str = Path(pattern=r"^[a-z]{3,12}$"),
    number: str = Path(pattern=r"^[0-9]{6}$"),
) -> dict[str, str]:
    """소유자 토큰 없이 지운다. 신고 대응용이다.

    파일을 먼저 지우고 레코드를 지운다. 순서가 반대면 레코드만 사라지고 파일이
    디스크에 영원히 남는다 — 목록에서도 안 보이므로 찾을 방법이 없어진다.
    """
    code = codes.normalize_code(word, number)
    transfer = repo.get_transfer(code)
    if transfer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="없는 코드입니다.")

    keys = [f["key"] for f in transfer.get("files", []) if f.get("key")]
    if keys:
        storage.delete_objects(keys)
    repo.delete_transfer(code)

    return {"status": "deleted", "code": code}


@router.get("/ping", dependencies=[rate_limit("lookup")], summary="관리자 기능 활성 여부")
def ping() -> dict[str, bool]:
    """토큰 없이도 부를 수 있다.

    관리자 페이지가 "토큰을 넣어라" 와 "이 서버는 관리자 기능이 꺼져 있다" 를
    구분해 안내하기 위한 것이고, 노출되는 정보는 이 라우터의 존재 여부뿐이다.
    라우터가 등록되지 않았다면 404 가 나므로 그것으로도 판별된다.
    """
    return {"enabled": True}
