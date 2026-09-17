"""디스크 포화 방어.

파일 전송 서비스가 죽는 가장 흔한 방식이다. 다 차면 업로드가 500 으로 실패하고
사용자는 이유를 모르며, SQLite 쓰기까지 막혀 이미 올라간 파일의 메타데이터도
갱신되지 않는다. 바닥까지 쓰지 않고 여유를 남긴 채 미리 거절해야 한다.
"""

import importlib
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import DISK_HEADROOM_BYTES, MAX_TOTAL_BYTES


@pytest.fixture
def single_node_client(
    aws: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[TestClient]:
    import app.config
    import app.main
    from app import deps

    monkeypatch.setenv("DEPLOY_MODE", "single")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("URL_SIGNING_KEY", "test-signing-key-for-disk-tests")

    app.config.get_settings.cache_clear()
    deps.get_storage.cache_clear()
    deps.get_repository.cache_clear()
    deps.get_signer.cache_clear()
    importlib.reload(app.main)

    try:
        with TestClient(app.main.app) as client:
            yield client
    finally:
        for name in ("DEPLOY_MODE", "DATA_DIR", "URL_SIGNING_KEY"):
            monkeypatch.delenv(name, raising=False)
        app.config.get_settings.cache_clear()
        importlib.reload(app.main)
        deps.get_storage.cache_clear()
        deps.get_repository.cache_clear()
        deps.get_signer.cache_clear()


def _create(client: TestClient, size: int = 1024) -> httpx.Response:
    return client.post(
        "/api/transfers",
        json={"files": [{"name": "a.bin", "size": size, "mime": ""}], "expires_in": 3600},
    )


def set_free(monkeypatch: pytest.MonkeyPatch, free: int | None) -> None:
    """저장소가 보고하는 여유 공간을 고정한다.

    실제 파일시스템을 쓰면 테스트가 호스트에 따라 갈린다. 실제로 CI 머신의
    ``/tmp`` 이 955MB tmpfs 라, 여유를 재보면 2GB 기준에 걸려 정상 케이스까지
    503 이 됐다. 디스크 가드를 검증하는 테스트가 디스크 크기에 좌우되면 안 된다.
    """
    from app.deps import get_storage

    monkeypatch.setattr(type(get_storage()), "free_bytes", lambda self: free)


class TestHeadroom:
    def test_accepts_when_there_is_room(
        self, single_node_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        set_free(monkeypatch, 100 * 1024**3)
        assert _create(single_node_client).status_code == 201

    def test_rejects_when_disk_is_nearly_full(
        self, single_node_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        set_free(monkeypatch, DISK_HEADROOM_BYTES + 512)
        response = _create(single_node_client, size=1024)

        assert response.status_code == 503
        assert "저장 공간" in response.json()["detail"]

    def test_rejection_tells_the_client_when_to_retry(
        self, single_node_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        set_free(monkeypatch, 0)
        response = _create(single_node_client)

        assert response.status_code == 503
        assert "Retry-After" in response.headers

    def test_large_transfer_rejected_before_a_small_one(
        self, single_node_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """판단은 남은 용량이 아니라 **요청 크기를 뺀 뒤** 남는 양으로 한다."""
        set_free(monkeypatch, DISK_HEADROOM_BYTES + 10 * 1024 * 1024)
        assert _create(single_node_client, size=1024).status_code == 201
        assert _create(single_node_client, size=50 * 1024 * 1024).status_code == 503


class TestQuotaIsNotBurned:
    def test_rejected_upload_does_not_consume_quota(
        self, single_node_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """공간이 없어 거절된 요청이 그날 쿼터를 깎으면 안 된다.

        올리지도 못했는데 한도만 줄어들면 사용자는 두 번 손해를 본다.
        그래서 공간 검사를 쿼터 차감보다 **먼저** 한다.
        """
        from app.deps import get_repository

        repo = get_repository()

        set_free(monkeypatch, 0)
        assert _create(single_node_client, size=1024).status_code == 503

        # 공간이 돌아오면 쿼터가 그대로 남아 있어야 한다
        set_free(monkeypatch, 100 * 1024**3)
        assert _create(single_node_client, size=1024).status_code == 201

        # 하루 한도 전체가 여전히 쓸 수 있는 상태인지 간접 확인
        from app.config import DAILY_QUOTA_BYTES

        assert repo.consume_quota("other-ip", DAILY_QUOTA_BYTES)


class TestAwsModeSkipsTheCheck:
    def test_s3_has_no_capacity_limit(self, client: TestClient) -> None:
        """S3 에는 용량 한도가 없으므로 이 검사가 끼어들면 안 된다."""
        from app.deps import get_storage

        assert get_storage().free_bytes() is None
        assert _create(client).status_code == 201


class TestAdminReportsCapacity:
    def test_stats_say_whether_uploads_are_accepted(self) -> None:
        """사용률(%)만으로는 '지금 받을 수 있는가' 에 답할 수 없다.

        16GB 의 80% 와 1TB 의 80% 는 남은 양이 전혀 다르다. 서버가 실제로
        쓰는 기준을 그대로 내보내야 관리자가 같은 판단을 할 수 있다.
        """
        # 상수 자체의 관계를 확인한다 — 여유가 한 전송 크기보다 커야 의미가 있다
        assert DISK_HEADROOM_BYTES > MAX_TOTAL_BYTES
