"""만료된 전송을 실제로 지우는 배치.

EventBridge 가 5분마다 깨운다. 이것이 파일이 사라지는 **주 경로**이고,
DynamoDB TTL 과 S3 라이프사이클은 이게 실패했을 때를 위한 백스톱이다.

한 번 실행에 처리할 양을 제한한다. 만료가 한꺼번에 몰려도 Lambda 시간
안에 끝나야 하고, 못 끝낸 것은 5분 뒤에 이어서 지우면 된다.
"""

import logging
import time
from typing import Any

from app.config import get_settings
from app.services.repository import Repository
from app.services.storage import S3Storage

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

#: 1회 실행 처리 상한
BATCH_LIMIT = 200


def sweep(now: int | None = None) -> dict[str, int]:
    settings = get_settings()
    repo = Repository(settings)
    storage = S3Storage(settings)

    cutoff = now if now is not None else int(time.time())
    expired = repo.expired_transfers(now=cutoff, limit=BATCH_LIMIT)

    deleted_objects = 0
    deleted_transfers = 0

    for transfer in expired:
        keys = [f["key"] for f in transfer.get("files", []) if f.get("key")]
        try:
            if keys:
                storage.delete_objects(keys)
                deleted_objects += len(keys)
            # 객체를 지운 뒤에 레코드를 지운다. 순서가 반대면 레코드만 사라지고
            # S3 객체가 영영 고아로 남는다 (S3 라이프사이클이 결국 치우긴 하지만
            # 그때까지 보관 비용이 나가고, 무엇보다 만료가 약속보다 늦어진다).
            repo.delete_transfer(transfer["code"])
            deleted_transfers += 1
        except Exception:
            # 한 건이 실패해도 나머지는 계속 지운다. 다음 실행에서 재시도된다.
            logger.exception("전송 정리 실패: %s", transfer.get("code"))

    logger.info(
        "sweep 완료: 전송 %d건, 객체 %d개 삭제", deleted_transfers, deleted_objects
    )
    return {"transfers": deleted_transfers, "objects": deleted_objects}


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, int]:
    return sweep()


if __name__ == "__main__":
    print(sweep())
