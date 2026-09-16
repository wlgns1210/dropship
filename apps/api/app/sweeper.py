"""만료된 전송을 실제로 지우는 배치.

EventBridge 가 5분마다 깨운다. 이것이 파일이 사라지는 **주 경로**이고,
DynamoDB TTL 과 S3 라이프사이클은 이게 실패했을 때를 위한 백스톱이다.

한 번 실행에 처리할 양을 제한한다. 만료가 한꺼번에 몰려도 Lambda 시간
안에 끝나야 하고, 못 끝낸 것은 5분 뒤에 이어서 지우면 된다.
"""

import contextlib
import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

#: 1회 실행 처리 상한
BATCH_LIMIT = 200


def sweep(now: int | None = None) -> dict[str, int]:
    # 배포 형태에 맞는 구현을 deps 가 골라준다. Sweeper 는 S3 인지 로컬
    # 디스크인지, DynamoDB 인지 SQLite 인지 알 필요가 없다.
    from app.deps import get_repository, get_storage

    repo = get_repository()
    storage = get_storage()

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

    # SQLite 에는 DynamoDB 의 TTL 같은 자동 만료가 없다. 쿼터·레이트리밋 행을
    # 여기서 같이 치우지 않으면 테이블이 무한히 자란다.
    purged = 0
    purge = getattr(repo, "purge_stale_counters", None)
    if purge is not None:
        try:
            purged = purge()
        except Exception:
            logger.exception("카운터 정리 실패")

    # 관리자 화면이 "마지막으로 언제 돌았고 무엇을 지웠는지" 를 볼 수 있게
    # 앱이 직접 기록한다. systemd 에 물어보면 실행 여부만 알 수 있고,
    # "돌긴 했는데 계속 아무것도 못 지운다" 같은 상태는 보이지 않는다.
    set_meta = getattr(repo, "set_meta", None)
    if set_meta is not None:
        with contextlib.suppress(Exception):
            set_meta(
                "last_sweep",
                json.dumps(
                    {
                        "at": cutoff,
                        "transfers": deleted_transfers,
                        "objects": deleted_objects,
                        "counters": purged,
                    }
                ),
            )

    logger.info(
        "sweep 완료: 전송 %d건, 객체 %d개, 카운터 %d행 삭제",
        deleted_transfers, deleted_objects, purged,
    )
    return {"transfers": deleted_transfers, "objects": deleted_objects, "counters": purged}


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, int]:
    return sweep()


if __name__ == "__main__":
    print(sweep())
