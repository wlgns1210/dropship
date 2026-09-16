"""단일 EC2 모드 전용 — 파트 업로드 수신과 다운로드 인가.

AWS 모드에서는 이 두 가지를 S3 가 presigned URL 로 직접 처리하므로 이 라우터가
등록되지 않는다. 여기서는 우리 서버가 대신한다.

바이트를 다루는 방식
--------------------
업로드는 **스트리밍으로 받아 디스크에 바로 쓴다.** 요청 본문을 통째로 메모리에
올리면 8MiB 파트 × 동시 업로더 수만큼 램이 사라진다.

다운로드는 **바이트를 아예 만지지 않는다.** 토큰만 확인하고 ``X-Accel-Redirect``
를 돌려주면 nginx 가 sendfile 로 보낸다. AWS 모드에서 S3 가 직접 보내던 것과
같은 성질을 유지한다.
"""

import hashlib

from fastapi import APIRouter, HTTPException, Request, Response, status

from app.config import PART_SIZE
from app.deps import StorageDep
from app.services.local_storage import LocalStorage

router = APIRouter(tags=["single-node"])

#: 파트 하나가 넘을 수 없는 크기. 마지막 파트가 정확히 PART_SIZE 일 수 있으므로
#: 약간의 여유를 둔다. 이 상한이 없으면 토큰 하나로 디스크를 채울 수 있다.
_MAX_PART_BYTES = PART_SIZE + 1024 * 1024

_BAD_TOKEN = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail="링크가 만료되었거나 올바르지 않습니다.",
)


@router.put("/api/upload/{token}", summary="파트 업로드 수신 (단일 노드)")
async def upload_part(token: str, request: Request, storage: StorageDep) -> Response:
    assert isinstance(storage, LocalStorage)

    target = storage.resolve_part_token(token)
    if target is None:
        raise _BAD_TOKEN

    # 토큰에 서명된 크기를 상한으로 쓴다. 전역 상한(PART_SIZE)만 쓰면
    # "1바이트짜리"라고 신고하고 8MB 를 밀어넣어 쿼터를 우회할 수 있다.
    # 0바이트 파일도 있으므로 최솟값을 두되, 여유는 최소한으로 잡는다.
    limit = min(_MAX_PART_BYTES, max(target.max_bytes, 1024))

    destination = storage.part_path(target.key, target.part_number)
    destination.parent.mkdir(parents=True, exist_ok=True)

    # S3 처럼 ETag 를 돌려준다. 프론트의 uploader.ts 가 이 값을 읽어 확정 요청에
    # 넣는데, 같은 출처라 CORS ExposeHeaders 걱정이 없다(S3 였다면 필수였다).
    digest = hashlib.md5(usedforsecurity=False)
    written = 0

    # 임시 파일에 받고 다 받은 뒤 옮긴다. 중간에 연결이 끊기면 반쪽 파트가
    # 정상 파트인 척 남는 것을 막는다. 재시도하면 덮어쓰면 된다.
    temporary = destination.with_suffix(".incoming")
    try:
        with temporary.open("wb") as handle:
            async for chunk in request.stream():
                written += len(chunk)
                if written > limit:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="파트 크기가 신고한 크기를 넘습니다.",
                    )
                digest.update(chunk)
                handle.write(chunk)
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    return Response(
        status_code=status.HTTP_200_OK,
        headers={"ETag": f'"{digest.hexdigest()}"'},
    )


@router.get("/api/d/{token}", summary="다운로드 인가 (단일 노드)")
def serve_download(token: str, storage: StorageDep) -> Response:
    assert isinstance(storage, LocalStorage)

    resolved = storage.resolve_download_token(token)
    if resolved is None:
        raise _BAD_TOKEN

    # 본문은 비어 있다. nginx 가 X-Accel-Redirect 를 보고 내부 경로의 파일을
    # 대신 보내며, 여기 담은 Content-Disposition 으로 원본 파일명을 복원한다.
    return Response(status_code=status.HTTP_200_OK, headers=storage.download_headers(resolved))
