"""로컬 개발용 AWS 리소스를 LocalStack 에 만든다.

    docker compose up -d
    python scripts/bootstrap_local.py

여러 번 돌려도 안전하다(이미 있으면 넘어간다).
"""

import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from app.config import get_settings

settings = get_settings()

_COMMON = {
    "region_name": settings.aws_region,
    "endpoint_url": settings.aws_endpoint_url,
}


def create_bucket() -> None:
    s3 = boto3.client("s3", **_COMMON)
    try:
        s3.create_bucket(
            Bucket=settings.s3_bucket,
            CreateBucketConfiguration={"LocationConstraint": settings.aws_region},
        )
        print(f"  버킷 생성: {settings.s3_bucket}")
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
            raise
        print(f"  버킷 이미 있음: {settings.s3_bucket}")

    # ── 브라우저 직접 업로드를 위한 CORS ──
    #
    # ExposeHeaders 의 "ETag" 가 핵심이다. 브라우저는 기본적으로 CORS 응답에서
    # 극소수 헤더만 JS 에 노출한다. ETag 를 열어주지 않으면 업로드 자체는
    # 성공하는데 클라이언트가 파트 ETag 를 읽지 못해 멀티파트를 닫을 수 없고,
    # "업로드는 됐는데 완료가 안 되는" 증상으로 나타난다.
    s3.put_bucket_cors(
        Bucket=settings.s3_bucket,
        CORSConfiguration={
            "CORSRules": [
                {
                    "AllowedOrigins": ["http://localhost:3000", "http://127.0.0.1:3000"],
                    "AllowedMethods": ["PUT", "GET", "HEAD"],
                    "AllowedHeaders": ["*"],
                    "ExposeHeaders": ["ETag"],
                    "MaxAgeSeconds": 3000,
                }
            ]
        },
    )
    print("  CORS 설정 완료 (ETag 노출 포함)")


def create_table() -> None:
    dynamodb = boto3.client("dynamodb", **_COMMON)
    try:
        dynamodb.create_table(
            TableName=settings.dynamodb_table,
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
                    # Sweeper 는 code 와 files[].key 만 있으면 되지만, 항목이 작아
                    # ALL 로 둬도 비용 차이가 없고 쿼리 후 재조회가 없어진다.
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
        )
        print(f"  테이블 생성: {settings.dynamodb_table}")
        dynamodb.get_waiter("table_exists").wait(TableName=settings.dynamodb_table)
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceInUseException":
            raise
        print(f"  테이블 이미 있음: {settings.dynamodb_table}")

    try:
        dynamodb.update_time_to_live(
            TableName=settings.dynamodb_table,
            TimeToLiveSpecification={"Enabled": True, "AttributeName": "ttl"},
        )
        print("  TTL 활성화 (백스톱용, 만료 판정은 앱이 한다)")
    except ClientError as exc:
        if "TimeToLive is already enabled" not in str(exc):
            raise


if __name__ == "__main__":
    print(f"LocalStack 부트스트랩 → {settings.aws_endpoint_url}")
    create_bucket()
    create_table()
    print("완료.")
