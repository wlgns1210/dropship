"""S3 접근 계층.

설계 메모
---------
1. **파일 바이트는 이 프로세스를 지나가지 않는다.** 여기서 하는 일은 presigned URL
   발급과 멀티파트 수명주기 관리뿐이다. 덕분에 API 를 Lambda 에 올려도
   API Gateway 의 페이로드 제한과 무관하다.

2. **S3 키에 파일명을 넣지 않는다.** 키는 ``<transfer_id>/<index>`` 로 끝이고
   실제 파일명은 DynamoDB 에 둔다. 한글·이모지·공백이 섞인 파일명을 키에
   인코딩하면서 생기는 문제(서명 불일치, 경로 조작)를 통째로 피할 수 있고,
   다운로드 시 ``Content-Disposition`` 으로 원본 이름을 그대로 복원한다.

3. **업로드는 항상 멀티파트다.** 작은 파일은 단일 PUT 이 더 싸지만, 경로를 하나로
   유지하는 편이 클라이언트·서버 양쪽에서 실수를 줄인다. 추가 비용은 요청당
   1원 미만이다.
"""

import contextlib
import math
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import boto3
from botocore.client import Config

from app.config import PART_SIZE, Settings


@dataclass(frozen=True)
class MultipartTarget:
    key: str
    upload_id: str
    part_size: int
    part_urls: list[str]


def build_object_key(transfer_id: str, file_index: int) -> str:
    """파일명을 배제한 S3 키. 위 설계 메모 2번 참고."""
    return f"{transfer_id}/{file_index}"


def content_disposition(filename: str) -> str:
    """한글 파일명이 깨지지 않는 첨부 헤더를 만든다.

    ``filename*=UTF-8''...`` (RFC 5987) 만 쓰고 ASCII fallback 은 두지 않는다.
    최신 브라우저는 모두 확장 형식을 이해하며, 두 값을 함께 주면 오히려
    일부 구형 브라우저가 ASCII 쪽을 골라 이름이 깨진다.
    """
    return f"attachment; filename*=UTF-8''{quote(filename, safe='')}"


def part_count_for(size: int) -> int:
    """0바이트 파일도 파트 1개는 있어야 멀티파트를 닫을 수 있다."""
    return max(1, math.ceil(size / PART_SIZE))


class S3Storage:
    def __init__(self, settings: Settings) -> None:
        self._bucket = settings.s3_bucket
        self._client = boto3.client(
            "s3",
            region_name=settings.aws_region,
            endpoint_url=settings.aws_endpoint_url,
            config=Config(
                signature_version="s3v4",
                s3={
                    # 실제 AWS 에서는 반드시 "virtual" 이어야 한다. "auto" 로 두면
                    # botocore 가 레거시 글로벌 엔드포인트(bucket.s3.amazonaws.com)로
                    # 서명한다. S3 는 거기에 307 리다이렉트로 리전 엔드포인트를
                    # 알려주는데, 클라이언트가 리다이렉트를 따라가면 Host 헤더가
                    # 바뀌고 서명은 원래 Host 로 계산돼 있어 SignatureDoesNotMatch
                    # 로 죽는다. 업로드가 통째로 실패한다.
                    #
                    # LocalStack 은 virtual-host 주소를 로컬에서 해석하지 못하므로
                    # 그때만 path 를 쓴다. 이 차이 때문에 로컬에서는 멀쩡하고
                    # 배포 후에만 터진다 — test_storage_endpoint.py 로 못 박아 둔다.
                    "addressing_style": "path" if settings.aws_endpoint_url else "virtual"
                },
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )

    # ── 업로드 ────────────────────────────────────────────────

    def start_multipart(
        self, key: str, mime: str, size: int, expires_in: int, filename: str
    ) -> MultipartTarget:
        """멀티파트를 열고 파트별 presigned PUT URL 을 만들어 돌려준다.

        ``ContentDisposition`` 을 **여기서** 객체에 박는 것이 중요하다. 운영에서는
        CloudFront 서명 URL 로 파일을 내려주는데, CloudFront 는 S3 처럼 응답 헤더를
        쿼리 파라미터로 덮어쓸 수 없다. 업로드 시점에 심어두지 않으면 다운로드된
        파일 이름이 S3 키(``0``, ``1``)로 떨어진다.
        """
        created = self._client.create_multipart_upload(
            Bucket=self._bucket,
            Key=key,
            ContentType=mime,
            ContentDisposition=content_disposition(filename),
        )
        upload_id: str = created["UploadId"]

        # 서명 URL 은 업로드에 걸릴 시간만큼만 살아 있으면 된다.
        # 보관 기간과 같게 두되 12시간을 넘기지 않는다.
        url_ttl = min(expires_in, 12 * 3600)

        part_urls = [
            self._client.generate_presigned_url(
                "upload_part",
                Params={
                    "Bucket": self._bucket,
                    "Key": key,
                    "UploadId": upload_id,
                    "PartNumber": part_number,
                },
                ExpiresIn=url_ttl,
            )
            for part_number in range(1, part_count_for(size) + 1)
        ]

        return MultipartTarget(
            key=key, upload_id=upload_id, part_size=PART_SIZE, part_urls=part_urls
        )

    def finish_multipart(self, key: str, upload_id: str, parts: list[dict[str, Any]]) -> None:
        """parts 는 ``[{"PartNumber": 1, "ETag": "..."}, ...]`` 형태여야 한다."""
        self._client.complete_multipart_upload(
            Bucket=self._bucket,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={"Parts": sorted(parts, key=lambda p: p["PartNumber"])},
        )

    def abort_multipart(self, key: str, upload_id: str) -> None:
        """실패한 업로드를 정리한다. 이미 없어도 조용히 넘어간다."""
        with contextlib.suppress(self._client.exceptions.ClientError):
            self._client.abort_multipart_upload(
                Bucket=self._bucket, Key=key, UploadId=upload_id
            )

    # ── 검증 · 삭제 ───────────────────────────────────────────

    def object_size(self, key: str) -> int | None:
        """객체가 실제로 올라왔는지 확인한다. 없으면 None."""
        try:
            head = self._client.head_object(Bucket=self._bucket, Key=key)
        except self._client.exceptions.ClientError:
            return None
        return int(head["ContentLength"])

    def free_bytes(self) -> int | None:
        """S3 는 용량 한도가 없다.

        None 을 돌려주면 호출부가 공간 검사를 건너뛴다. 단일 노드의 로컬
        디스크에만 있는 제약이라, 그쪽 구현에만 실제 값이 있다.
        """
        return None

    def open_stream(self, key: str, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        """S3 객체를 조각내어 읽는다. ZIP 일괄 다운로드가 쓴다.

        전체를 메모리에 올리지 않도록 StreamingBody 를 그대로 흘린다.
        """
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        body = response["Body"]
        try:
            while True:
                chunk = body.read(chunk_size)
                if not chunk:
                    return
                yield chunk
        finally:
            body.close()

    def delete_objects(self, keys: list[str]) -> None:
        """DeleteObjects 는 한 번에 1000개까지라 나눠서 보낸다."""
        for start in range(0, len(keys), 1000):
            chunk = keys[start : start + 1000]
            if not chunk:
                continue
            self._client.delete_objects(
                Bucket=self._bucket,
                Delete={"Objects": [{"Key": k} for k in chunk], "Quiet": True},
            )

    # ── 다운로드 ──────────────────────────────────────────────

    def presign_download(self, key: str, filename: str, mime: str, ttl: int) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self._bucket,
                "Key": key,
                "ResponseContentDisposition": content_disposition(filename),
                "ResponseContentType": mime,
            },
            ExpiresIn=ttl,
        )
