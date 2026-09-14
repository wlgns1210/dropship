"""다운로드 URL 서명.

두 가지 구현을 둔다.

``LocalSigner``
    S3 presigned GET. 로컬 개발과 LocalStack 용이며, 응답 헤더를 쿼리
    파라미터로 덮어쓸 수 있어 파일명을 요청 시점에 지정할 수 있다.

``CloudFrontSigner``
    운영용. CloudFront 서명 URL 은 **응답 헤더를 쿼리로 덮어쓸 수 없다.**
    그래서 파일명(``Content-Disposition``)은 업로드 시점에 S3 객체
    메타데이터로 박아 둔다 (``S3Storage.start_multipart`` 참고).
    이 차이를 모르고 짜면 운영에서만 파일명이 ``0``, ``1`` 로 떨어진다.
"""

from datetime import UTC, datetime, timedelta
from typing import Protocol

from botocore.signers import CloudFrontSigner as _BotoCloudFrontSigner
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.config import Settings
from app.services.storage import S3Storage


class Signer(Protocol):
    def sign(self, *, key: str, filename: str, mime: str, ttl: int) -> str: ...


class LocalSigner:
    def __init__(self, storage: S3Storage) -> None:
        self._storage = storage

    def sign(self, *, key: str, filename: str, mime: str, ttl: int) -> str:
        return self._storage.presign_download(key, filename, mime, ttl)


class CloudFrontSigner:
    def __init__(self, settings: Settings) -> None:
        if not settings.cloudfront_domain or not settings.cloudfront_key_pair_id:
            raise ValueError("CloudFront 서명에는 도메인과 키페어 ID가 필요합니다")

        private_key = serialization.load_pem_private_key(
            settings.cloudfront_private_key.replace("\\n", "\n").encode(),
            password=None,
        )
        # load_pem_private_key 는 어떤 종류의 키든 돌려준다. CloudFront 는 RSA 만
        # 받으므로 여기서 걸러낸다. 이걸 안 하면 EC 키를 넣었을 때 배포 후
        # 첫 다운로드에서야 알 수 없는 예외로 터진다.
        if not isinstance(private_key, rsa.RSAPrivateKey):
            raise ValueError(
                f"CloudFront 서명 키는 RSA 여야 합니다 (받은 것: {type(private_key).__name__})"
            )

        def rsa_signer(message: bytes) -> bytes:
            # CloudFront 는 RSA-SHA1 만 받는다. 여기서 SHA256 을 쓰면 403 이 난다.
            return private_key.sign(message, padding.PKCS1v15(), hashes.SHA1())

        self._domain = settings.cloudfront_domain.rstrip("/")
        self._signer = _BotoCloudFrontSigner(settings.cloudfront_key_pair_id, rsa_signer)

    def sign(self, *, key: str, filename: str, mime: str, ttl: int) -> str:
        # filename·mime 은 여기서 쓰지 않는다. 객체에 이미 박혀 있다.
        url = f"https://{self._domain}/{key}"
        expires = datetime.now(UTC) + timedelta(seconds=ttl)
        return self._signer.generate_presigned_url(url, date_less_than=expires)


def build_signer(settings: Settings, storage: S3Storage) -> Signer:
    if settings.download_signer == "cloudfront":
        return CloudFrontSigner(settings)
    return LocalSigner(storage)
