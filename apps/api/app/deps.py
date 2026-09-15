"""FastAPI 의존성.

저장소와 리포지토리는 배포 형태에 따라 구현이 갈린다. 라우터는 어느 쪽인지
알지 못하고, 알 필요도 없다.

    deploy_mode = "aws"     S3          + DynamoDB
    deploy_mode = "single"  로컬 파일   + SQLite

클라이언트 객체는 캐시한다. Lambda 콜드스타트를 아끼기 위해서고, 단일 EC2
모드에서는 SQLite 커넥션과 스키마 초기화를 매 요청 반복하지 않기 위해서다.
"""

from functools import lru_cache
from typing import Annotated, Any, Protocol

from fastapi import Depends, HTTPException, Request, status

from app.config import RATE_LIMITS, Settings, get_settings
from app.services.security import client_ip, hash_ip
from app.services.signing import Signer
from app.services.storage import MultipartTarget


class Storage(Protocol):
    """S3Storage 와 LocalStorage 가 함께 만족하는 계약.

    시그니처를 정확히 적는다. ``**kwargs: Any`` 로 뭉개면 두 구현이 서로 다른
    인자를 받게 되어도 타입 검사가 잡지 못하고, 배포 형태를 바꿀 때 런타임에서야
    터진다. 이 Protocol 이 곧 두 구현이 지켜야 할 문서다.
    """

    def start_multipart(
        self, key: str, mime: str, size: int, expires_in: int, filename: str
    ) -> MultipartTarget: ...
    def finish_multipart(
        self, key: str, upload_id: str, parts: list[dict[str, Any]]
    ) -> None: ...
    def abort_multipart(self, key: str, upload_id: str) -> None: ...
    def object_size(self, key: str) -> int | None: ...
    def delete_objects(self, keys: list[str]) -> None: ...
    def presign_download(self, key: str, filename: str, mime: str, ttl: int) -> str: ...


class Repo(Protocol):
    """Repository(DynamoDB)와 SqliteRepository 가 함께 만족하는 계약."""

    def create_transfer(
        self,
        *,
        code: str,
        transfer_id: str,
        owner_token: str,
        files: list[dict[str, Any]],
        total_size: int,
        created_at: int,
        expires_at: int,
        creator_ip_hash: str,
    ) -> None: ...
    def get_transfer(self, code: str) -> dict[str, Any] | None: ...
    def mark_ready(
        self, *, code: str, owner_token: str, files: list[dict[str, Any]], total_size: int
    ) -> bool: ...
    def bump_download_count(self, code: str) -> None: ...
    def delete_transfer(self, code: str, owner_token: str | None = None) -> bool: ...
    def expired_transfers(self, *, now: int, limit: int = 100) -> list[dict[str, Any]]: ...
    def consume_quota(self, ip_hash: str, num_bytes: int) -> bool: ...
    def refund_quota(self, ip_hash: str, num_bytes: int) -> None: ...
    def allow_request(self, *, scope: str, ip_hash: str, limit: int, window: int) -> bool: ...


@lru_cache
def get_storage() -> Storage:
    settings = get_settings()
    if settings.is_single_node:
        from app.services.local_storage import LocalStorage

        return LocalStorage(settings)

    from app.services.storage import S3Storage

    return S3Storage(settings)


@lru_cache
def get_repository() -> Repo:
    settings = get_settings()
    if settings.is_single_node:
        from app.services.sqlite_repository import SqliteRepository

        return SqliteRepository(settings)

    from app.services.repository import Repository

    return Repository(settings)


@lru_cache
def get_signer() -> Signer:
    """다운로드 URL 서명자.

    두 모드 모두 ``LocalSigner`` 를 쓴다 — 저장소가 만들어 준 URL 을 그대로
    넘기는 얇은 어댑터라, S3 presigned 든 우리 서버의 토큰 URL 이든 상관없다.
    CloudFront 서명 URL 을 쓸 때만 다른 구현이 된다.
    """
    from app.services.signing import build_signer

    return build_signer(get_settings(), get_storage())


SettingsDep = Annotated[Settings, Depends(get_settings)]
StorageDep = Annotated[Storage, Depends(get_storage)]
RepositoryDep = Annotated[Repo, Depends(get_repository)]
SignerDep = Annotated[Any, Depends(get_signer)]


def get_ip_hash(request: Request, settings: SettingsDep) -> str:
    return hash_ip(client_ip(request), settings.ip_hash_salt)


IpHashDep = Annotated[str, Depends(get_ip_hash)]


def rate_limit(scope: str) -> Any:
    """스코프별 레이트리밋 의존성을 만든다.

    단일 EC2 모드에서는 이것이 유일한 방어선이다(WAF 도 CloudFront 도 없다).
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
