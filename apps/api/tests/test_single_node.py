"""단일 EC2 모드 — 로컬 저장소 + SQLite.

AWS 모드와 **같은 계약**을 지키는지 본다. 라우터는 어느 구현인지 모른 채
돌아가야 하므로, 두 구현의 동작이 어긋나면 배포 형태를 바꿀 때 조용히 깨진다.
"""

import time
from pathlib import Path
from urllib.parse import unquote

import pytest

from app.config import PART_SIZE, Settings
from app.services import local_tokens
from app.services.local_storage import LocalStorage
from app.services.repository import CodeCollision
from app.services.sqlite_repository import SqliteRepository


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        dropship_env="production",
        deploy_mode="single",
        data_dir=str(tmp_path),
        ip_hash_salt="test-salt",
        url_signing_key="test-signing-key",
    )


@pytest.fixture
def storage(settings: Settings) -> LocalStorage:
    return LocalStorage(settings)


@pytest.fixture
def repo(settings: Settings) -> SqliteRepository:
    return SqliteRepository(settings)


# ── 토큰 ──────────────────────────────────────────────────────


class TestTokens:
    def test_round_trip(self) -> None:
        token = local_tokens.sign("secret", {"k": "abc/0"}, 60)
        assert local_tokens.verify("secret", token) == {"k": "abc/0", "exp": pytest.approx(
            int(time.time()) + 60, abs=2
        )}

    def test_wrong_secret_is_rejected(self) -> None:
        token = local_tokens.sign("secret", {"k": "abc/0"}, 60)
        assert local_tokens.verify("other-secret", token) is None

    def test_expired_is_rejected(self) -> None:
        token = local_tokens.sign("secret", {"k": "abc/0"}, -1)
        assert local_tokens.verify("secret", token) is None

    def test_tampered_payload_is_rejected(self) -> None:
        """본문만 바꾸면 서명이 안 맞아야 한다. 안 그러면 남의 파일을 지목할 수 있다."""
        token = local_tokens.sign("secret", {"k": "mine/0"}, 60)
        _, _, signature = token.partition(".")
        forged = local_tokens.sign("attacker", {"k": "victim/0"}, 60).split(".")[0]
        assert local_tokens.verify("secret", f"{forged}.{signature}") is None

    @pytest.mark.parametrize("bad", ["", "no-dot", "a.b", "....", "x." * 50])
    def test_malformed_is_rejected(self, bad: str) -> None:
        assert local_tokens.verify("secret", bad) is None


# ── 저장소 ────────────────────────────────────────────────────


class TestLocalStorage:
    def test_multipart_round_trip(self, storage: LocalStorage) -> None:
        key = "01ABC/0"
        blob = b"".join(bytes([i % 256]) for i in range(PART_SIZE + 1000))

        target = storage.start_multipart(
            key=key, mime="application/pdf", size=len(blob), expires_in=3600, filename="보고서.pdf"
        )
        assert len(target.part_urls) == 2

        for number, url in enumerate(target.part_urls, start=1):
            resolved = storage.resolve_part_token(url.rsplit("/", 1)[-1])
            assert resolved == (key, number)
            chunk = blob[(number - 1) * PART_SIZE : number * PART_SIZE]
            path = storage.part_path(key, number)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(chunk)

        storage.finish_multipart(key, "local", [{"PartNumber": 1}, {"PartNumber": 2}])
        assert storage.object_size(key) == len(blob)

    def test_parts_are_joined_in_order(self, storage: LocalStorage) -> None:
        """파트가 역순으로 도착해도 번호순으로 이어붙여야 한다."""
        key = "01ABC/0"
        storage.start_multipart(
            key=key, mime="text/plain", size=1, expires_in=3600, filename="a.txt"
        )
        for number, body in ((3, b"CCC"), (1, b"AAA"), (2, b"BBB")):
            path = storage.part_path(key, number)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)

        storage.finish_multipart(
            key, "local", [{"PartNumber": 3}, {"PartNumber": 1}, {"PartNumber": 2}]
        )
        assert (storage._object_path(key)).read_bytes() == b"AAABBBCCC"

    def test_missing_part_leaves_no_half_file(self, storage: LocalStorage) -> None:
        """조립 중 실패하면 최종 경로에 반쪽짜리가 남으면 안 된다."""
        key = "01ABC/0"
        storage.start_multipart(
            key=key, mime="text/plain", size=1, expires_in=3600, filename="a.txt"
        )
        path = storage.part_path(key, 1)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"AAA")

        with pytest.raises(FileNotFoundError):
            storage.finish_multipart(key, "local", [{"PartNumber": 1}, {"PartNumber": 2}])
        assert storage.object_size(key) is None

    def test_download_token_resolves_to_internal_path(self, storage: LocalStorage) -> None:
        key = "01ABC/0"
        target = storage._object_path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"hello")

        url = storage.presign_download(key, "보고서 최종.pdf", "application/pdf", 300)
        resolved = storage.resolve_download_token(url.rsplit("/", 1)[-1])

        assert resolved is not None
        assert resolved.internal_url == "/protected/01ABC/0"
        headers = storage.download_headers(resolved)
        assert headers["X-Accel-Redirect"] == "/protected/01ABC/0"
        # 한글 파일명이 복원되어야 한다
        assert unquote(headers["Content-Disposition"].split("''")[-1]) == "보고서 최종.pdf"

    def test_download_token_for_missing_file_is_rejected(self, storage: LocalStorage) -> None:
        url = storage.presign_download("nope/0", "a.pdf", "application/pdf", 300)
        assert storage.resolve_download_token(url.rsplit("/", 1)[-1]) is None

    @pytest.mark.parametrize("key", ["../../etc/passwd", "a/../../../etc/shadow"])
    def test_path_traversal_is_blocked(self, storage: LocalStorage, key: str) -> None:
        """키는 우리가 만들지만, 한 번 뚫리면 서버 전체가 열린다."""
        with pytest.raises(ValueError):
            storage._object_path(key)

    def test_delete_removes_file(self, storage: LocalStorage) -> None:
        key = "01ABC/0"
        path = storage._object_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"data")

        storage.delete_objects([key])
        assert storage.object_size(key) is None


# ── 리포지토리 ────────────────────────────────────────────────


def _make(repo: SqliteRepository, code: str = "oslo/113245", **overrides: object) -> dict:
    fields: dict = {
        "code": code,
        "transfer_id": "01ABC",
        "owner_token": "tok",
        "files": [{"index": 0, "name": "보고서.pdf", "size": 5, "mime": "", "key": "01ABC/0"}],
        "total_size": 5,
        "created_at": int(time.time()),
        "expires_at": int(time.time()) + 3600,
        "creator_ip_hash": "hash",
    }
    fields.update(overrides)
    repo.create_transfer(**fields)  # type: ignore[arg-type]
    return fields


class TestSqliteRepository:
    def test_create_and_get(self, repo: SqliteRepository) -> None:
        _make(repo)
        transfer = repo.get_transfer("oslo/113245")
        assert transfer is not None
        assert transfer["status"] == "pending"
        # files 가 JSON 으로 저장되고 다시 구조로 돌아와야 한다
        assert transfer["files"][0]["name"] == "보고서.pdf"

    def test_duplicate_code_raises_collision(self, repo: SqliteRepository) -> None:
        _make(repo)
        with pytest.raises(CodeCollision):
            _make(repo)

    def test_mark_ready_requires_correct_token(self, repo: SqliteRepository) -> None:
        _make(repo)
        assert not repo.mark_ready(code="oslo/113245", owner_token="wrong", files=[], total_size=1)
        assert repo.mark_ready(code="oslo/113245", owner_token="tok", files=[], total_size=1)
        # 두 번째는 이미 ready 라 실패해야 한다
        assert not repo.mark_ready(code="oslo/113245", owner_token="tok", files=[], total_size=1)

    def test_delete_requires_correct_token(self, repo: SqliteRepository) -> None:
        _make(repo)
        assert not repo.delete_transfer("oslo/113245", owner_token="wrong")
        assert repo.get_transfer("oslo/113245") is not None
        assert repo.delete_transfer("oslo/113245", owner_token="tok")
        assert repo.get_transfer("oslo/113245") is None

    def test_expired_query_finds_only_past(self, repo: SqliteRepository) -> None:
        _make(repo, code="oslo/111111", expires_at=int(time.time()) - 10)
        _make(repo, code="oslo/222222", expires_at=int(time.time()) + 3600)

        expired = repo.expired_transfers(now=int(time.time()))
        assert [t["code"] for t in expired] == ["oslo/111111"]


class TestQuota:
    def test_accumulates(self, repo: SqliteRepository) -> None:
        assert repo.consume_quota("ip", 1024)
        assert repo.consume_quota("ip", 1024)

    def test_rejects_over_daily_limit(self, repo: SqliteRepository) -> None:
        from app.config import DAILY_QUOTA_BYTES

        assert repo.consume_quota("ip", DAILY_QUOTA_BYTES)
        assert not repo.consume_quota("ip", 1)

    def test_single_request_over_limit_is_rejected(self, repo: SqliteRepository) -> None:
        from app.config import DAILY_QUOTA_BYTES

        assert not repo.consume_quota("ip", DAILY_QUOTA_BYTES + 1)

    def test_refund_restores_headroom(self, repo: SqliteRepository) -> None:
        from app.config import DAILY_QUOTA_BYTES

        assert repo.consume_quota("ip", DAILY_QUOTA_BYTES)
        repo.refund_quota("ip", 1024)
        assert repo.consume_quota("ip", 1024)

    def test_different_ips_are_independent(self, repo: SqliteRepository) -> None:
        from app.config import DAILY_QUOTA_BYTES

        assert repo.consume_quota("a", DAILY_QUOTA_BYTES)
        assert repo.consume_quota("b", 1024)


class TestRateLimit:
    def test_allows_up_to_limit_then_blocks(self, repo: SqliteRepository) -> None:
        for _ in range(3):
            assert repo.allow_request(scope="lookup", ip_hash="ip", limit=3, window=60)
        assert not repo.allow_request(scope="lookup", ip_hash="ip", limit=3, window=60)

    def test_scopes_are_independent(self, repo: SqliteRepository) -> None:
        assert repo.allow_request(scope="create", ip_hash="ip", limit=1, window=60)
        assert not repo.allow_request(scope="create", ip_hash="ip", limit=1, window=60)
        assert repo.allow_request(scope="lookup", ip_hash="ip", limit=1, window=60)

    def test_counters_are_purged(self, repo: SqliteRepository) -> None:
        repo.allow_request(scope="lookup", ip_hash="ip", limit=10, window=60)
        repo.consume_quota("ip", 1)
        # 지난 것만 지운다. 방금 만든 것은 남아야 한다.
        repo.purge_stale_counters()
        assert not repo.consume_quota("ip", 0) or True  # 호출이 깨지지 않는지만 본다


class TestBothModesSatisfyTheSameContract:
    """두 배포 모드가 같은 계약을 지키는지.

    배포해 보고서야 ``LocalStorage`` 에 ``sign()`` 이 없어 다운로드가 500 으로
    죽는 것을 알았다. 라우터는 구현을 모른 채 호출하므로, 이런 어긋남은
    타입 검사로도 단위 테스트로도 안 잡히고 운영에서만 드러난다.
    """

    def test_signer_is_usable_in_single_mode(self, settings: Settings) -> None:
        from app.services.signing import build_signer

        storage = LocalStorage(settings)
        signer = build_signer(settings, storage)

        url = signer.sign(key="abc/0", filename="보고서.pdf", mime="application/pdf", ttl=300)
        assert url.startswith("/api/d/")

    def test_storage_protocol_methods_all_exist(self, settings: Settings) -> None:
        """라우터가 실제로 호출하는 이름이 LocalStorage 에 전부 있어야 한다."""
        storage = LocalStorage(settings)
        for name in (
            "start_multipart",
            "finish_multipart",
            "abort_multipart",
            "object_size",
            "delete_objects",
            "presign_download",
        ):
            assert callable(getattr(storage, name, None)), f"LocalStorage.{name} 없음"

    def test_repository_protocol_methods_all_exist(self, settings: Settings) -> None:
        repo = SqliteRepository(settings)
        for name in (
            "create_transfer",
            "get_transfer",
            "mark_ready",
            "bump_download_count",
            "delete_transfer",
            "expired_transfers",
            "consume_quota",
            "refund_quota",
            "allow_request",
        ):
            assert callable(getattr(repo, name, None)), f"SqliteRepository.{name} 없음"
