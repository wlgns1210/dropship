"""FastAPI 의존성. 클라이언트 객체는 Lambda 콜드스타트를 아끼려고 캐시한다."""

from functools import lru_cache
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status

from app.config import RATE_LIMITS, Settings, get_settings
from app.services.repository import Repository
from app.services.security import client_ip, hash_ip
from app.services.signing import Signer, build_signer
from app.services.storage import S3Storage


@lru_cache
def get_storage() -> S3Storage:
    return S3Storage(get_settings())


@lru_cache
def get_repository() -> Repository:
    return Repository(get_settings())


@lru_cache
def get_signer() -> Signer:
    return build_signer(get_settings(), get_storage())


SettingsDep = Annotated[Settings, Depends(get_settings)]
StorageDep = Annotated[S3Storage, Depends(get_storage)]
RepositoryDep = Annotated[Repository, Depends(get_repository)]
SignerDep = Annotated[Signer, Depends(get_signer)]


def get_ip_hash(request: Request, settings: SettingsDep) -> str:
    return hash_ip(client_ip(request), settings.ip_hash_salt)


IpHashDep = Annotated[str, Depends(get_ip_hash)]


def rate_limit(scope: str) -> Any:
    """스코프별 레이트리밋 의존성을 만든다.

    WAF 가 앞단에서 한 번 걸러주지만, 여기 있는 것이 최종 방어선이다.
    WAF 룰이 빠지거나 잘못 붙어도 애플리케이션이 스스로를 지켜야 한다.
    """
    limit, window = RATE_LIMITS[scope]

    def _check(repo: RepositoryDep, ip_hash: IpHashDep) -> None:
        if not repo.allow_request(scope=scope, ip_hash=ip_hash, limit=limit, window=window):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="요청이 너무 잦습니다. 잠시 후 다시 시도해 주세요.",
                headers={"Retry-After": str(window)},
            )

    return Depends(_check)
