"""전송 생성 · 확정 · 조회 · 다운로드 · 삭제."""

import time
from typing import Any

from fastapi import APIRouter, HTTPException, Path, status
from ulid import ULID

from app.config import DOWNLOAD_URL_TTL, MAX_TOTAL_BYTES
from app.deps import IpHashDep, RepositoryDep, SignerDep, StorageDep, rate_limit
from app.schemas import (
    CompleteTransferRequest,
    CompleteTransferResponse,
    CreateTransferRequest,
    CreateTransferResponse,
    DeleteResponse,
    DownloadResponse,
    FileUpload,
    PublicFile,
    TransferInfoResponse,
)
from app.services import codes
from app.services.repository import CodeCollision, Repository
from app.services.security import flatten_timing, is_risky, sanitize_filename
from app.services.storage import S3Storage, build_object_key, part_count_for

router = APIRouter(prefix="/api/transfers", tags=["transfers"])

#: 코드 충돌 시 재시도 횟수. 코드 공간이 넓어 1회 충돌도 드물다.
_CODE_ATTEMPTS = 5

WordPath = Path(pattern=r"^[a-z]{3,12}$")
NumberPath = Path(pattern=r"^[0-9]{6}$")

#: 존재하지 않는 코드와 만료된 코드에 똑같이 돌려주는 응답.
#: 둘을 구분해 주면 유효한 코드를 골라내는 열거 공격이 쉬워진다.
_GONE = HTTPException(
    status_code=status.HTTP_410_GONE,
    detail="링크를 찾을 수 없거나 이미 만료되었습니다.",
)


def _load_live_transfer(repo: Repository, code: str) -> dict[str, Any]:
    """수신자에게 보여줄 수 있는 상태인지 확인하고 전송을 가져온다.

    만료 판정은 여기서 ``expires_at`` 비교로 한다. Sweeper 가 아직 안 돌았거나
    DynamoDB TTL 이 늦어도 만료된 링크는 이 시점에 즉시 죽는다.
    """
    if not codes.is_valid_code(code):
        raise _GONE
    transfer = repo.get_transfer(code)
    if transfer is None:
        raise _GONE
    if transfer.get("status") != "ready":
        # pending = 업로드가 아직 안 끝났다. 반쪽짜리를 노출하지 않는다.
        raise _GONE
    if int(transfer["expires_at"]) <= int(time.time()):
        raise _GONE
    return transfer


# ── 생성 ──────────────────────────────────────────────────────


@router.post(
    "",
    response_model=CreateTransferResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[rate_limit("create")],
    summary="업로드 세션 생성",
)
def create_transfer(
    payload: CreateTransferRequest,
    repo: RepositoryDep,
    storage: StorageDep,
    ip_hash: IpHashDep,
) -> CreateTransferResponse:
    total_size = payload.total_size

    if not repo.consume_quota(ip_hash, total_size):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="오늘 업로드 한도(5GB)를 모두 사용했습니다. 내일 다시 시도해 주세요.",
        )

    transfer_id = str(ULID())
    opened: list[tuple[str, str]] = []  # 실패 시 되돌릴 (key, upload_id)

    try:
        uploads: list[FileUpload] = []
        stored_files: list[dict[str, Any]] = []

        for index, spec in enumerate(payload.files):
            safe_name = sanitize_filename(spec.name)
            key = build_object_key(transfer_id, index)
            target = storage.start_multipart(
                key=key,
                mime=spec.mime,
                size=spec.size,
                expires_in=payload.expires_in,
                filename=safe_name,
            )
            opened.append((key, target.upload_id))

            uploads.append(
                FileUpload(
                    file_index=index,
                    key=key,
                    upload_id=target.upload_id,
                    part_size=target.part_size,
                    part_urls=target.part_urls,
                )
            )
            stored_files.append(
                {
                    "index": index,
                    "name": safe_name,
                    "size": spec.size,
                    "mime": spec.mime,
                    "key": key,
                    "upload_id": target.upload_id,
                }
            )

        now = int(time.time())
        expires_at = now + payload.expires_in
        owner_token = codes.generate_owner_token()
        code = _reserve_code(
            repo,
            transfer_id=transfer_id,
            owner_token=owner_token,
            files=stored_files,
            total_size=total_size,
            created_at=now,
            expires_at=expires_at,
            creator_ip_hash=ip_hash,
        )
    except Exception:
        # 열어둔 멀티파트를 닫고 차감한 쿼터를 돌려준다.
        for key, upload_id in opened:
            storage.abort_multipart(key, upload_id)
        repo.refund_quota(ip_hash, total_size)
        raise

    return CreateTransferResponse(
        transfer_id=transfer_id,
        code=code,
        owner_token=owner_token,
        expires_at=expires_at,
        uploads=uploads,
    )


def _reserve_code(repo: Repository, **fields: Any) -> str:
    """비어 있는 코드를 찾을 때까지 재시도한다."""
    for attempt in range(_CODE_ATTEMPTS):
        code = codes.generate_code()
        try:
            repo.create_transfer(code=code, **fields)
        except CodeCollision:
            if attempt == _CODE_ATTEMPTS - 1:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="링크를 만들지 못했습니다. 잠시 후 다시 시도해 주세요.",
                ) from None
            continue
        return code
    raise AssertionError("unreachable")


# ── 확정 ──────────────────────────────────────────────────────


@router.post(
    "/{word}/{number}/complete",
    response_model=CompleteTransferResponse,
    summary="업로드 확정",
)
def complete_transfer(
    payload: CompleteTransferRequest,
    repo: RepositoryDep,
    storage: StorageDep,
    word: str = WordPath,
    number: str = NumberPath,
) -> CompleteTransferResponse:
    code = codes.normalize_code(word, number)

    transfer = repo.get_transfer(code)
    if transfer is None or transfer.get("status") != "pending":
        raise _GONE
    # 토큰 비교는 mark_ready 의 조건부 업데이트에서 원자적으로 한 번 더 한다.
    if payload.owner_token != transfer.get("owner_token"):
        raise _GONE

    stored: list[dict[str, Any]] = transfer["files"]
    by_index = {int(f["index"]): f for f in stored}
    submitted = {c.file_index: c for c in payload.files}

    if set(submitted) != set(by_index):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="업로드한 파일 목록이 처음 요청과 다릅니다.",
        )

    for index, completion in submitted.items():
        record = by_index[index]
        expected_parts = part_count_for(int(record["size"]))
        if len(completion.parts) != expected_parts:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{record['name']}: 업로드된 조각 수가 맞지 않습니다.",
            )
        storage.finish_multipart(
            key=record["key"],
            upload_id=record["upload_id"],
            parts=[{"PartNumber": p.part_number, "ETag": p.etag} for p in completion.parts],
        )

    # 클라이언트가 선언한 크기를 믿지 않는다. S3 에 실제로 올라간 크기가 기준이다.
    verified, actual_total = _verify_uploads(storage, stored)

    if actual_total > MAX_TOTAL_BYTES:
        storage.delete_objects([f["key"] for f in stored])
        repo.delete_transfer(code)
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="실제 업로드된 용량이 1GB를 넘습니다.",
        )

    if not repo.mark_ready(
        code=code, owner_token=payload.owner_token, files=verified, total_size=actual_total
    ):
        raise _GONE

    return CompleteTransferResponse(
        code=code,
        expires_at=int(transfer["expires_at"]),
        total_size=actual_total,
    )


def _verify_uploads(
    storage: S3Storage, stored: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], int]:
    """모든 객체가 실제로 존재하는지 확인하고 실제 크기로 바꿔 담는다."""
    verified: list[dict[str, Any]] = []
    total = 0
    for record in stored:
        actual = storage.object_size(record["key"])
        if actual is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{record['name']}: 업로드가 완료되지 않았습니다.",
            )
        total += actual
        verified.append({**record, "size": actual, "upload_id": None})
    return verified, total


# ── 조회 ──────────────────────────────────────────────────────


@router.get(
    "/{word}/{number}",
    response_model=TransferInfoResponse,
    dependencies=[rate_limit("lookup")],
    summary="수신자용 전송 정보",
)
async def get_transfer(
    repo: RepositoryDep,
    word: str = WordPath,
    number: str = NumberPath,
) -> TransferInfoResponse:
    started = time.perf_counter()
    code = codes.normalize_code(word, number)
    try:
        transfer = _load_live_transfer(repo, code)
    except HTTPException:
        await flatten_timing(started)
        raise

    await flatten_timing(started)
    return TransferInfoResponse(
        code=code,
        files=[
            PublicFile(
                index=int(f["index"]),
                name=f["name"],
                size=int(f["size"]),
                mime=f["mime"],
                risky=is_risky(f["name"]),
            )
            for f in transfer["files"]
        ],
        total_size=int(transfer["total_size"]),
        created_at=int(transfer["created_at"]),
        expires_at=int(transfer["expires_at"]),
    )


# ── 다운로드 ──────────────────────────────────────────────────


@router.get(
    "/{word}/{number}/download/{index}",
    response_model=DownloadResponse,
    dependencies=[rate_limit("download")],
    summary="다운로드용 서명 URL 발급",
)
def download_file(
    repo: RepositoryDep,
    signer: SignerDep,
    word: str = WordPath,
    number: str = NumberPath,
    index: int = Path(ge=0),
) -> DownloadResponse:
    code = codes.normalize_code(word, number)
    transfer = _load_live_transfer(repo, code)

    match = next((f for f in transfer["files"] if int(f["index"]) == index), None)
    if match is None:
        raise _GONE

    url = signer.sign(
        key=match["key"],
        filename=match["name"],
        mime=match["mime"],
        ttl=DOWNLOAD_URL_TTL,
    )
    # 통계용. 횟수로 접근을 막지 않으므로 실패해도 그냥 넘어간다.
    repo.bump_download_count(code)

    return DownloadResponse(url=url, expires_in=DOWNLOAD_URL_TTL)


# ── 삭제 ──────────────────────────────────────────────────────


@router.delete(
    "/{word}/{number}",
    response_model=DeleteResponse,
    summary="업로더가 직접 삭제",
)
def delete_transfer(
    repo: RepositoryDep,
    storage: StorageDep,
    owner_token: str,
    word: str = WordPath,
    number: str = NumberPath,
) -> DeleteResponse:
    code = codes.normalize_code(word, number)
    transfer = repo.get_transfer(code)
    if transfer is None:
        raise _GONE

    if not repo.delete_transfer(code, owner_token=owner_token):
        raise _GONE

    storage.delete_objects([f["key"] for f in transfer.get("files", [])])
    return DeleteResponse()
