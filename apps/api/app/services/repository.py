"""DynamoDB 단일 테이블 저장소.

키 설계
-------
====================================  ========  ===================================
PK                                    SK        용도
====================================  ========  ===================================
``T#<code>``                          ``META``  전송 1건
``Q#<ip_hash>#<YYYY-MM-DD>``          ``QUOTA`` IP 일일 업로드 쿼터
``R#<scope>#<ip_hash>#<window>``      ``RL``    레이트리밋 카운터
====================================  ========  ===================================

GSI1(``GSI1PK`` = ``EXPIRY``, ``GSI1SK`` = ``expires_at``) 로 Sweeper 가 만료 건만
정확히 긁어간다. 풀스캔이 필요 없다.

모든 항목에 ``ttl`` 을 넣지만, **TTL 은 만료 수단이 아니다.** DynamoDB TTL 은
삭제까지 최대 48시간이 걸린다. 실제 만료 판정은 ``expires_at`` 비교로 하고
TTL 은 Sweeper 가 실패했을 때를 위한 백스톱일 뿐이다.
"""

import contextlib
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from app.config import DAILY_QUOTA_BYTES, Settings

_TTL_GRACE_SECONDS = 3600


class CodeCollision(Exception):
    """이미 쓰이고 있는 코드. 호출부가 다른 코드로 재시도하면 된다."""


def _plain(value: Any) -> Any:
    """DynamoDB 가 돌려주는 Decimal 을 int/float 로 되돌린다."""
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value


class Repository:
    def __init__(self, settings: Settings) -> None:
        self._table = boto3.resource(
            "dynamodb",
            region_name=settings.aws_region,
            endpoint_url=settings.aws_endpoint_url,
        ).Table(settings.dynamodb_table)

    # ── 전송 ──────────────────────────────────────────────────

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
    ) -> None:
        """코드가 비어 있을 때만 쓴다. 충돌하면 CodeCollision."""
        try:
            self._table.put_item(
                Item={
                    "PK": f"T#{code}",
                    "SK": "META",
                    "code": code,
                    "transfer_id": transfer_id,
                    "owner_token": owner_token,
                    "status": "pending",
                    "files": files,
                    "total_size": total_size,
                    "created_at": created_at,
                    "expires_at": expires_at,
                    "download_count": 0,
                    "creator_ip_hash": creator_ip_hash,
                    # Phase 5 대비 자리. MVP 에서는 항상 None 이다.
                    "password_hash": None,
                    "max_downloads": None,
                    "ttl": expires_at + _TTL_GRACE_SECONDS,
                    "GSI1PK": "EXPIRY",
                    "GSI1SK": expires_at,
                },
                ConditionExpression="attribute_not_exists(PK)",
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise CodeCollision(code) from exc
            raise

    def get_transfer(self, code: str) -> dict[str, Any] | None:
        result = self._table.get_item(Key={"PK": f"T#{code}", "SK": "META"})
        item = result.get("Item")
        return _plain(item) if item else None

    def mark_ready(
        self, *, code: str, owner_token: str, files: list[dict[str, Any]], total_size: int
    ) -> bool:
        """pending → ready. 토큰이 틀리거나 이미 ready 면 False.

        ``status`` 는 DynamoDB 예약어라 ExpressionAttributeNames 로 우회한다.
        """
        try:
            self._table.update_item(
                Key={"PK": f"T#{code}", "SK": "META"},
                UpdateExpression=(
                    "SET #status = :ready, #files = :files, total_size = :total"
                ),
                ConditionExpression=(
                    "attribute_exists(PK) AND #status = :pending AND owner_token = :token"
                ),
                ExpressionAttributeNames={"#status": "status", "#files": "files"},
                ExpressionAttributeValues={
                    ":ready": "ready",
                    ":pending": "pending",
                    ":files": files,
                    ":total": total_size,
                    ":token": owner_token,
                },
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise
        return True

    def bump_download_count(self, code: str) -> None:
        """통계용. 실패해도 다운로드를 막지 않으므로 예외를 삼킨다."""
        with contextlib.suppress(ClientError):
            self._table.update_item(
                Key={"PK": f"T#{code}", "SK": "META"},
                UpdateExpression="ADD download_count :one",
                ExpressionAttributeValues={":one": 1},
            )

    def delete_transfer(self, code: str, owner_token: str | None = None) -> bool:
        """owner_token 을 주면 소유자 확인 후 삭제. 안 주면 무조건 삭제(Sweeper 용)."""
        kwargs: dict[str, Any] = {"Key": {"PK": f"T#{code}", "SK": "META"}}
        if owner_token is not None:
            kwargs["ConditionExpression"] = "owner_token = :token"
            kwargs["ExpressionAttributeValues"] = {":token": owner_token}
        try:
            self._table.delete_item(**kwargs)
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise
        return True

    def expired_transfers(self, *, now: int, limit: int = 100) -> list[dict[str, Any]]:
        """만료 시각이 지난 전송을 오래된 것부터 가져온다."""
        result = self._table.query(
            IndexName="GSI1",
            KeyConditionExpression=Key("GSI1PK").eq("EXPIRY") & Key("GSI1SK").lt(now),
            Limit=limit,
        )
        return [_plain(item) for item in result.get("Items", [])]

    # ── 쿼터 ──────────────────────────────────────────────────

    def consume_quota(self, ip_hash: str, num_bytes: int) -> bool:
        """하루 허용량 안에서만 증가시킨다. 넘으면 False 이고 아무것도 쓰지 않는다.

        읽고-판단하고-쓰는 대신 조건부 업데이트 하나로 처리한다. 동시에 여러 건을
        올려도 허용량을 넘길 수 없다.
        """
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        headroom = DAILY_QUOTA_BYTES - num_bytes
        if headroom < 0:
            return False
        try:
            self._table.update_item(
                Key={"PK": f"Q#{ip_hash}#{today}", "SK": "QUOTA"},
                UpdateExpression="SET #ttl = :ttl ADD bytes_used :n",
                ConditionExpression=(
                    "attribute_not_exists(bytes_used) OR bytes_used <= :headroom"
                ),
                ExpressionAttributeNames={"#ttl": "ttl"},
                ExpressionAttributeValues={
                    ":n": num_bytes,
                    ":headroom": headroom,
                    ":ttl": int(time.time()) + 2 * 86400,
                },
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise
        return True

    def refund_quota(self, ip_hash: str, num_bytes: int) -> None:
        """업로드 세션 생성에 실패했을 때 차감분을 되돌린다."""
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        with contextlib.suppress(ClientError):
            self._table.update_item(
                Key={"PK": f"Q#{ip_hash}#{today}", "SK": "QUOTA"},
                UpdateExpression="ADD bytes_used :n",
                ExpressionAttributeValues={":n": -num_bytes},
            )

    # ── 레이트리밋 ────────────────────────────────────────────

    def allow_request(self, *, scope: str, ip_hash: str, limit: int, window: int) -> bool:
        """고정 윈도우 카운터. 허용이면 True.

        윈도우 경계에서 최대 2배까지 통과할 수 있는 방식이지만, 목적이
        '자동화된 열거를 실용 불가능하게 만드는 것' 이라 이 정도로 충분하다.
        """
        bucket = int(time.time()) // window
        try:
            self._table.update_item(
                Key={"PK": f"R#{scope}#{ip_hash}#{bucket}", "SK": "RL"},
                UpdateExpression="SET #ttl = :ttl ADD hits :one",
                ConditionExpression="attribute_not_exists(hits) OR hits < :limit",
                ExpressionAttributeNames={"#ttl": "ttl"},
                ExpressionAttributeValues={
                    ":one": 1,
                    ":limit": limit,
                    ":ttl": (bucket + 2) * window,
                },
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise
        return True
