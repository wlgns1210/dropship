"""단일 EC2 모드의 메타데이터 저장소. ``Repository``(DynamoDB)와 같은 인터페이스.

DynamoDB 판이 조건부 업데이트로 보장하던 것들을 여기서는 트랜잭션으로 지킨다.
특히 쿼터와 레이트리밋은 **읽고-판단하고-쓰는 사이에 끼어들 수 없어야** 한다.
동시에 여러 요청이 들어와도 하루 한도를 넘길 수 없어야 하기 때문이다.

WAL 모드를 켠다. 기본 저널 모드에서는 쓰기가 읽기를 통째로 막아서, 업로드
확정 한 건이 수신자 조회를 전부 대기시킨다.
"""

import contextlib
import json
import sqlite3
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import DAILY_QUOTA_BYTES, Settings
from app.services.repository import CodeCollision

_TTL_GRACE_SECONDS = 3600

_SCHEMA = """
CREATE TABLE IF NOT EXISTS transfers (
    code            TEXT PRIMARY KEY,
    transfer_id     TEXT NOT NULL,
    owner_token     TEXT NOT NULL,
    status          TEXT NOT NULL,
    files           TEXT NOT NULL,          -- JSON
    total_size      INTEGER NOT NULL,
    created_at      INTEGER NOT NULL,
    expires_at      INTEGER NOT NULL,
    download_count  INTEGER NOT NULL DEFAULT 0,
    creator_ip_hash TEXT NOT NULL,
    password_hash   TEXT,                   -- Phase 5 대비 자리
    max_downloads   INTEGER                 -- Phase 5 대비 자리
);

-- Sweeper 가 만료 건만 정확히 긁어가게 한다. DynamoDB 의 GSI1 과 같은 역할.
CREATE INDEX IF NOT EXISTS idx_transfers_expires ON transfers(expires_at);

CREATE TABLE IF NOT EXISTS quotas (
    ip_hash    TEXT NOT NULL,
    day        TEXT NOT NULL,
    bytes_used INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (ip_hash, day)
);

CREATE TABLE IF NOT EXISTS rate_limits (
    scope   TEXT NOT NULL,
    ip_hash TEXT NOT NULL,
    bucket  INTEGER NOT NULL,
    hits    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (scope, ip_hash, bucket)
);

-- 관리자 화면이 읽는 운영 메모. 지금은 Sweeper 마지막 실행 기록만 쓴다.
-- systemd 에 물어봐도 되지만, 앱이 스스로 남긴 값이어야 "돌긴 했는데 아무것도
-- 못 지웠다" 같은 상태까지 알 수 있다.
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class SqliteRepository:
    def __init__(self, settings: Settings) -> None:
        self._path = Path(settings.sqlite_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # 커넥션은 스레드마다 따로 둔다. uvicorn 이 동기 라우트를 스레드풀에서
        # 돌리기 때문에, 하나를 공유하면 sqlite3 가 스레드 위반으로 거부한다.
        self._local = threading.local()
        with self._connect() as connection:
            connection.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection: sqlite3.Connection | None = getattr(self._local, "connection", None)
        if connection is None:
            connection = sqlite3.connect(self._path, timeout=15, isolation_level=None)
            connection.row_factory = sqlite3.Row
            # WAL: 쓰기가 읽기를 막지 않는다. 이게 없으면 업로드 확정 한 건이
            # 수신자 조회를 전부 대기시킨다.
            connection.execute("PRAGMA journal_mode=WAL")
            # 쓰기 잠금이 잡혀 있으면 즉시 실패하지 말고 기다린다.
            connection.execute("PRAGMA busy_timeout=15000")
            connection.execute("PRAGMA foreign_keys=ON")
            # NORMAL 은 WAL 에서 충분히 안전하면서 fsync 횟수를 줄인다.
            connection.execute("PRAGMA synchronous=NORMAL")
            self._local.connection = connection
        return connection

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
        try:
            self._connect().execute(
                """INSERT INTO transfers
                   (code, transfer_id, owner_token, status, files, total_size,
                    created_at, expires_at, creator_ip_hash)
                   VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?)""",
                (
                    code, transfer_id, owner_token, json.dumps(files, ensure_ascii=False),
                    total_size, created_at, expires_at, creator_ip_hash,
                ),
            )
        except sqlite3.IntegrityError as exc:
            # PRIMARY KEY 충돌 = 이미 쓰이는 코드. 호출부가 다른 코드로 재시도한다.
            raise CodeCollision(code) from exc

    def get_transfer(self, code: str) -> dict[str, Any] | None:
        row = self._connect().execute(
            "SELECT * FROM transfers WHERE code = ?", (code,)
        ).fetchone()
        return _to_dict(row) if row else None

    def mark_ready(
        self, *, code: str, owner_token: str, files: list[dict[str, Any]], total_size: int
    ) -> bool:
        """pending → ready. 토큰이 틀리거나 이미 ready 면 False.

        조건을 WHERE 에 담아 한 문장으로 처리한다. 확인하고 나서 쓰면 그 사이에
        다른 요청이 끼어들 수 있다.
        """
        cursor = self._connect().execute(
            """UPDATE transfers SET status = 'ready', files = ?, total_size = ?
               WHERE code = ? AND status = 'pending' AND owner_token = ?""",
            (json.dumps(files, ensure_ascii=False), total_size, code, owner_token),
        )
        return cursor.rowcount > 0

    def bump_download_count(self, code: str) -> None:
        """통계용. 실패해도 다운로드를 막지 않는다."""
        with contextlib.suppress(sqlite3.Error):
            self._connect().execute(
                "UPDATE transfers SET download_count = download_count + 1 WHERE code = ?",
                (code,),
            )

    def delete_transfer(self, code: str, owner_token: str | None = None) -> bool:
        if owner_token is None:
            cursor = self._connect().execute("DELETE FROM transfers WHERE code = ?", (code,))
        else:
            cursor = self._connect().execute(
                "DELETE FROM transfers WHERE code = ? AND owner_token = ?",
                (code, owner_token),
            )
        return cursor.rowcount > 0

    def expired_transfers(self, *, now: int, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._connect().execute(
            "SELECT * FROM transfers WHERE expires_at < ? ORDER BY expires_at LIMIT ?",
            (now, limit),
        ).fetchall()
        return [_to_dict(row) for row in rows]

    # ── 쿼터 ──────────────────────────────────────────────────

    def consume_quota(self, ip_hash: str, num_bytes: int) -> bool:
        """하루 허용량 안에서만 증가시킨다. 넘으면 False 이고 아무것도 쓰지 않는다."""
        if num_bytes > DAILY_QUOTA_BYTES:
            return False

        day = datetime.now(UTC).strftime("%Y-%m-%d")
        connection = self._connect()
        # BEGIN IMMEDIATE 로 쓰기 잠금을 먼저 잡는다. 이게 없으면 두 요청이
        # 동시에 잔여량을 읽고 둘 다 통과시켜 한도를 넘길 수 있다.
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute(
                "SELECT bytes_used FROM quotas WHERE ip_hash = ? AND day = ?",
                (ip_hash, day),
            ).fetchone()
            used = row["bytes_used"] if row else 0
            if used + num_bytes > DAILY_QUOTA_BYTES:
                connection.execute("ROLLBACK")
                return False

            connection.execute(
                """INSERT INTO quotas (ip_hash, day, bytes_used) VALUES (?, ?, ?)
                   ON CONFLICT(ip_hash, day)
                   DO UPDATE SET bytes_used = bytes_used + excluded.bytes_used""",
                (ip_hash, day, num_bytes),
            )
            connection.execute("COMMIT")
            return True
        except sqlite3.Error:
            connection.execute("ROLLBACK")
            raise

    def refund_quota(self, ip_hash: str, num_bytes: int) -> None:
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        with contextlib.suppress(sqlite3.Error):
            self._connect().execute(
                """UPDATE quotas SET bytes_used = MAX(0, bytes_used - ?)
                   WHERE ip_hash = ? AND day = ?""",
                (num_bytes, ip_hash, day),
            )

    # ── 레이트리밋 ────────────────────────────────────────────

    def allow_request(self, *, scope: str, ip_hash: str, limit: int, window: int) -> bool:
        bucket = int(time.time()) // window
        connection = self._connect()
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute(
                "SELECT hits FROM rate_limits WHERE scope = ? AND ip_hash = ? AND bucket = ?",
                (scope, ip_hash, bucket),
            ).fetchone()
            if row and row["hits"] >= limit:
                connection.execute("ROLLBACK")
                return False

            connection.execute(
                """INSERT INTO rate_limits (scope, ip_hash, bucket, hits) VALUES (?, ?, ?, 1)
                   ON CONFLICT(scope, ip_hash, bucket) DO UPDATE SET hits = hits + 1""",
                (scope, ip_hash, bucket),
            )
            connection.execute("COMMIT")
            return True
        except sqlite3.Error:
            connection.execute("ROLLBACK")
            raise

    # ── 관리자 지표 ───────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        """관리자 화면이 쓰는 집계.

        쿼리를 여러 번 던지지 않고 한 번에 모은다. 5초마다 폴링되는 화면이라
        여기서 테이블을 여러 번 훑으면 그 자체가 부하가 된다.
        """
        connection = self._connect()
        now = int(time.time())
        today_start = now - (now % 86400)

        by_status = {
            row["status"]: row["n"]
            for row in connection.execute(
                "SELECT status, COUNT(*) AS n FROM transfers GROUP BY status"
            ).fetchall()
        }

        ready = connection.execute(
            """SELECT COUNT(*) AS n, COALESCE(SUM(total_size), 0) AS bytes,
                      COALESCE(SUM(download_count), 0) AS downloads
               FROM transfers WHERE status = 'ready' AND expires_at > ?""",
            (now,),
        ).fetchone()

        today = connection.execute(
            """SELECT COUNT(*) AS n, COALESCE(SUM(total_size), 0) AS bytes
               FROM transfers WHERE created_at >= ?""",
            (today_start,),
        ).fetchone()

        expiring = connection.execute(
            "SELECT COUNT(*) AS n FROM transfers WHERE expires_at BETWEEN ? AND ?",
            (now, now + 3600),
        ).fetchone()

        # 이미 만료됐는데 아직 안 지워진 것. 이 수가 계속 늘면 Sweeper 가
        # 죽었다는 뜻이고, 곧 디스크가 찬다.
        overdue = connection.execute(
            "SELECT COUNT(*) AS n FROM transfers WHERE expires_at < ?", (now,)
        ).fetchone()

        quota_today = connection.execute(
            """SELECT COUNT(*) AS ips, COALESCE(SUM(bytes_used), 0) AS bytes
               FROM quotas WHERE day = ?""",
            (datetime.now(UTC).strftime("%Y-%m-%d"),),
        ).fetchone()

        return {
            "transfers": {
                "ready": by_status.get("ready", 0),
                "pending": by_status.get("pending", 0),
                "live": ready["n"],
                "expiring_1h": expiring["n"],
                "overdue": overdue["n"],
            },
            "storage": {"tracked_bytes": ready["bytes"]},
            "activity": {
                "downloads_total": ready["downloads"],
                "uploads_today": today["n"],
                "bytes_today": today["bytes"],
                "unique_ips_today": quota_today["ips"],
                "quota_bytes_today": quota_today["bytes"],
            },
            "last_sweep": self.get_meta("last_sweep"),
        }

    def list_transfers(
        self, *, limit: int = 50, offset: int = 0, status: str | None = None
    ) -> dict[str, Any]:
        """관리자 목록용. 최신순.

        여기서 나가는 값에는 **파일명과 공유 코드가 들어 있다.** 신고 대응에
        필요한 정보지만, 지표 엔드포인트와 달리 민감하므로 라우터를 분리해
        두었다. 업로더는 IP 해시의 앞 8자만 내보낸다 — 같은 사람이 반복해
        올리는지는 볼 수 있으면서 원본 IP 는 복원되지 않는다.
        """
        connection = self._connect()
        where = "WHERE status = ?" if status else ""
        params: tuple[Any, ...] = (status,) if status else ()

        total = connection.execute(
            f"SELECT COUNT(*) AS n FROM transfers {where}", params
        ).fetchone()["n"]

        rows = connection.execute(
            # transfer_id 를 보조 정렬 키로 쓴다. created_at 은 초 단위라 같은 초에
            # 만들어진 전송끼리는 순서가 정해지지 않고, 그러면 목록이 새로고침마다
            # 뒤바뀐다. ULID 는 밀리초 정밀도로 사전순 정렬되도록 설계돼 있어
            # 별도 컬럼 없이 그대로 쓸 수 있다.
            f"""SELECT code, status, files, total_size, created_at, expires_at,
                       download_count, creator_ip_hash
                FROM transfers {where}
                ORDER BY created_at DESC, transfer_id DESC LIMIT ? OFFSET ?""",
            (*params, limit, offset),
        ).fetchall()

        items = []
        for row in rows:
            files = json.loads(row["files"])
            items.append(
                {
                    "code": row["code"],
                    "status": row["status"],
                    "total_size": row["total_size"],
                    "created_at": row["created_at"],
                    "expires_at": row["expires_at"],
                    "download_count": row["download_count"],
                    "uploader": row["creator_ip_hash"][:8],
                    "files": [
                        {
                            "index": f["index"],
                            "name": f["name"],
                            "size": f["size"],
                            "mime": f.get("mime", ""),
                        }
                        for f in files
                    ],
                }
            )

        return {"items": items, "total": total, "offset": offset, "limit": limit}

    def get_meta(self, key: str) -> str | None:
        row = self._connect().execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with contextlib.suppress(sqlite3.Error):
            self._connect().execute(
                """INSERT INTO meta (key, value) VALUES (?, ?)
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
                (key, value),
            )

    # ── 정리 ──────────────────────────────────────────────────

    def purge_stale_counters(self) -> int:
        """지난 쿼터·레이트리밋 행을 치운다.

        DynamoDB 는 TTL 이 알아서 지워주지만 SQLite 에는 그런 게 없다. Sweeper 가
        같이 호출한다. 안 하면 테이블이 무한히 자란다.
        """
        cutoff_day = datetime.fromtimestamp(time.time() - 2 * 86400, UTC).strftime("%Y-%m-%d")
        connection = self._connect()
        removed = connection.execute("DELETE FROM quotas WHERE day < ?", (cutoff_day,)).rowcount
        removed += connection.execute(
            "DELETE FROM rate_limits WHERE bucket < ?", (int(time.time() - 86400) // 60,)
        ).rowcount
        return removed


def _to_dict(row: sqlite3.Row) -> dict[str, Any]:
    record = dict(row)
    record["files"] = json.loads(record["files"])
    record["ttl"] = int(record["expires_at"]) + _TTL_GRACE_SECONDS
    return record
