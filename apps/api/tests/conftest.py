"""테스트 픽스처.

moto 로 S3 와 DynamoDB 를 프로세스 안에서 흉내 낸다. Docker 도 LocalStack 도
필요 없어서 CI 에서 그대로 돈다.
"""

import os
from collections.abc import Iterator

import boto3
import pytest

# moto 가 실제 자격증명을 집어들지 않도록 임포트 전에 환경을 막아둔다.
os.environ.update(
    {
        "SKIFF_ENV": "local",
        "AWS_REGION": "ap-northeast-2",
        "AWS_ENDPOINT_URL": "",
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SESSION_TOKEN": "testing",
        "AWS_DEFAULT_REGION": "ap-northeast-2",
        "S3_BUCKET": "skiff-test",
        "DYNAMODB_TABLE": "skiff-test",
        "IP_HASH_SALT": "test-salt",
        "DOWNLOAD_SIGNER": "local",
    }
)

from fastapi.testclient import TestClient
from moto import mock_aws

REGION = "ap-northeast-2"
BUCKET = "skiff-test"
TABLE = "skiff-test"


@pytest.fixture(autouse=True)
def _ignore_disk_headroom(monkeypatch: pytest.MonkeyPatch) -> None:
    """디스크 여유 검사를 기본적으로 끈다.

    검사 자체는 운영에서 중요하지만(디스크가 차면 서비스가 죽는다) 대부분의
    테스트와는 무관하다. 그런데 실제 파일시스템을 재면 테스트가 호스트에
    좌우된다 — 이 CI 머신의 ``/tmp`` 이 955MB tmpfs 라 여유가 2GB 기준에
    못 미쳐서, 디스크와 아무 상관없는 테스트들이 503 으로 무더기 실패했다.

    None 은 "용량 한도를 모르는 저장소" 를 뜻하고 호출부가 검사를 건너뛴다.
    가드 자체를 검증하는 테스트는 test_disk_guard.py 에서 다시 덮어쓴다.
    """
    from app.services.local_storage import LocalStorage

    monkeypatch.setattr(LocalStorage, "free_bytes", lambda self: None)


@pytest.fixture
def aws() -> Iterator[None]:
    with mock_aws():
        boto3.client("s3", region_name=REGION).create_bucket(
            Bucket=BUCKET,
            CreateBucketConfiguration={"LocationConstraint": REGION},
        )
        boto3.client("dynamodb", region_name=REGION).create_table(
            TableName=TABLE,
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
                {"AttributeName": "GSI1PK", "AttributeType": "S"},
                {"AttributeName": "GSI1SK", "AttributeType": "N"},
            ],
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "GSI1",
                    "KeySchema": [
                        {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                        {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
        )
        yield


@pytest.fixture
def client(aws: None) -> Iterator[TestClient]:
    """의존성 캐시를 비우고 새 TestClient 를 만든다.

    deps 의 lru_cache 가 moto 컨텍스트 밖에서 만든 boto3 클라이언트를 들고 있으면
    테스트끼리 오염된다.
    """
    from app import deps
    from app.main import app

    deps.get_storage.cache_clear()
    deps.get_repository.cache_clear()
    deps.get_signer.cache_clear()

    with TestClient(app) as test_client:
        yield test_client

    deps.get_storage.cache_clear()
    deps.get_repository.cache_clear()
    deps.get_signer.cache_clear()
