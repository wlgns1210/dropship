"""presigned URL 이 **리전** 엔드포인트를 가리키는지.

이 테스트가 있는 이유
---------------------
``addressing_style`` 을 ``"auto"`` 로 두면 botocore 가 레거시 글로벌 엔드포인트
``<bucket>.s3.amazonaws.com`` 으로 서명한다. S3 는 거기에 307 리다이렉트로 리전
엔드포인트를 알려주는데, 클라이언트가 리다이렉트를 따라가면 Host 헤더가 바뀌고
서명은 원래 Host 로 계산돼 있어 ``SignatureDoesNotMatch`` 가 난다. 업로드가
통째로 실패한다.

로컬 개발은 LocalStack 을 쓰고 LocalStack 은 path 스타일이라 이 문제가 절대
드러나지 않는다. 실제로 배포하고 나서야 터졌다. 그래서 테스트로 고정한다.
"""

from urllib.parse import urlparse

import pytest

from app.config import Settings
from app.services.storage import S3Storage

REGION = "ap-northeast-2"


@pytest.fixture
def production_settings() -> Settings:
    """실제 AWS 를 쓰는 설정 (커스텀 엔드포인트 없음)."""
    return Settings(
        skiff_env="production",
        aws_region=REGION,
        aws_endpoint_url=None,
        s3_bucket="skiff-test",
    )


class TestRegionalEndpoint:
    def test_presigned_url_host_includes_region(
        self, aws: None, production_settings: Settings
    ) -> None:
        storage = S3Storage(production_settings)
        url = storage.presign_download("some/key", "파일.pdf", "application/pdf", 300)

        host = urlparse(url).netloc
        assert f".s3.{REGION}.amazonaws.com" in host, (
            f"리전 엔드포인트가 아님: {host}. "
            "글로벌 엔드포인트로 서명하면 307 리다이렉트 후 서명이 깨진다."
        )

    def test_presigned_url_is_not_legacy_global(
        self, aws: None, production_settings: Settings
    ) -> None:
        storage = S3Storage(production_settings)
        url = storage.presign_download("some/key", "파일.pdf", "application/pdf", 300)

        host = urlparse(url).netloc
        assert not host.endswith(".s3.amazonaws.com"), (
            f"레거시 글로벌 엔드포인트로 서명됨: {host}"
        )

    def test_upload_part_urls_are_also_regional(
        self, aws: None, production_settings: Settings
    ) -> None:
        """다운로드만이 아니라 업로드 URL 도 같아야 한다. 실제로 깨진 쪽은 업로드였다."""
        # 버킷은 aws 픽스처가 이미 만들어 둔다.
        storage = S3Storage(production_settings)
        target = storage.start_multipart(
            key="abc/0", mime="application/pdf", size=1024, expires_in=3600, filename="a.pdf"
        )

        for url in target.part_urls:
            host = urlparse(url).netloc
            assert f".s3.{REGION}.amazonaws.com" in host, f"파트 URL 이 글로벌 엔드포인트: {host}"


class TestLocalStackStillUsesPathStyle:
    def test_custom_endpoint_keeps_path_addressing(self, aws: None) -> None:
        """LocalStack 은 virtual-host 주소를 해석하지 못하므로 path 스타일이어야 한다."""
        settings = Settings(
            skiff_env="local",
            aws_region=REGION,
            aws_endpoint_url="http://localhost:4566",
            s3_bucket="skiff-test",
        )
        storage = S3Storage(settings)
        url = storage.presign_download("some/key", "파일.pdf", "application/pdf", 300)

        parsed = urlparse(url)
        assert parsed.netloc == "localhost:4566"
        # path 스타일이면 버킷 이름이 호스트가 아니라 경로에 들어간다
        assert parsed.path.startswith("/skiff-test/")
