"""관리자 API.

공개 익명 서비스에 붙는 화면이라 인증이 새면 서버 상태가 통째로 노출된다.
막는 쪽을 먼저 확인한다.
"""

import importlib
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

TOKEN = "test-admin-token-value-0123456789"


@pytest.fixture
def admin_client(
    aws: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[TestClient]:
    """관리자 토큰이 켜진 단일 노드 모드 앱."""
    import app.config
    import app.main
    from app import deps

    monkeypatch.setenv("ADMIN_TOKEN", TOKEN)
    monkeypatch.setenv("DEPLOY_MODE", "single")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("URL_SIGNING_KEY", "test-signing-key-for-admin-tests")

    app.config.get_settings.cache_clear()
    deps.get_storage.cache_clear()
    deps.get_repository.cache_clear()
    deps.get_signer.cache_clear()
    importlib.reload(app.main)

    try:
        with TestClient(app.main.app) as client:
            yield client
    finally:
        for name in ("ADMIN_TOKEN", "DEPLOY_MODE", "DATA_DIR", "URL_SIGNING_KEY"):
            monkeypatch.delenv(name, raising=False)
        app.config.get_settings.cache_clear()
        importlib.reload(app.main)
        deps.get_storage.cache_clear()
        deps.get_repository.cache_clear()
        deps.get_signer.cache_clear()


class TestAuth:
    def test_no_token_is_unauthorized(self, admin_client: TestClient) -> None:
        assert admin_client.get("/api/admin/stats").status_code == 401

    def test_wrong_token_is_unauthorized(self, admin_client: TestClient) -> None:
        response = admin_client.get(
            "/api/admin/stats", headers={"Authorization": "Bearer wrong-token"}
        )
        assert response.status_code == 401

    def test_token_without_bearer_prefix_is_rejected(self, admin_client: TestClient) -> None:
        response = admin_client.get("/api/admin/stats", headers={"Authorization": TOKEN})
        assert response.status_code == 401

    def test_correct_token_succeeds(self, admin_client: TestClient) -> None:
        response = admin_client.get(
            "/api/admin/stats", headers={"Authorization": f"Bearer {TOKEN}"}
        )
        assert response.status_code == 200

    def test_prefix_of_token_is_rejected(self, admin_client: TestClient) -> None:
        """부분 일치가 통과하면 토큰을 앞에서부터 맞춰갈 수 있다."""
        response = admin_client.get(
            "/api/admin/stats", headers={"Authorization": f"Bearer {TOKEN[:-1]}"}
        )
        assert response.status_code == 401


class TestDisabledByDefault:
    def test_router_is_absent_without_token(self, client: TestClient) -> None:
        """토큰을 설정하지 않은 서버에는 경로 자체가 없어야 한다.

        401 이 아니라 404 다. 인증으로 막는 것보다 존재하지 않는 편이 안전하다.
        """
        assert client.get("/api/admin/stats").status_code == 404


class TestPayload:
    def test_reports_system_and_services(self, admin_client: TestClient) -> None:
        body = admin_client.get(
            "/api/admin/stats", headers={"Authorization": f"Bearer {TOKEN}"}
        ).json()

        assert body["deploy_mode"] == "single"
        assert "system" in body and "services" in body
        assert body["policy"]["max_total_bytes"] == 1024**3

    def test_reports_app_metrics_in_single_mode(self, admin_client: TestClient) -> None:
        body = admin_client.get(
            "/api/admin/stats", headers={"Authorization": f"Bearer {TOKEN}"}
        ).json()

        assert "transfers" in body
        assert set(body["transfers"]) >= {"ready", "pending", "live", "expiring_1h", "overdue"}
        assert "activity" in body

    def test_stats_does_not_leak_filenames_or_codes(self, admin_client: TestClient) -> None:
        """**지표** 엔드포인트는 집계만 내보내야 한다.

        목록(/transfers)은 신고 대응을 위해 파일명과 코드를 의도적으로 내보내지만,
        지표만 필요한 쪽까지 따라 나가면 안 된다. 그래서 경로를 나눠 두었고,
        이 테스트가 그 경계를 지킨다.
        """
        created = admin_client.post(
            "/api/transfers",
            json={
                "files": [{"name": "비밀문서.pdf", "size": 10, "mime": "application/pdf"}],
                "expires_in": 3600,
            },
        )
        assert created.status_code == 201
        code = created.json()["code"]

        raw = admin_client.get(
            "/api/admin/stats", headers={"Authorization": f"Bearer {TOKEN}"}
        ).text

        assert "비밀문서" not in raw
        assert code not in raw
        assert "owner_token" not in raw
        assert "creator_ip_hash" not in raw


class TestStatsAccuracy:
    def test_counts_reflect_actual_transfers(self, admin_client: TestClient) -> None:
        for _ in range(3):
            admin_client.post(
                "/api/transfers",
                json={"files": [{"name": "a.txt", "size": 5, "mime": ""}], "expires_in": 3600},
            )

        body = admin_client.get(
            "/api/admin/stats", headers={"Authorization": f"Bearer {TOKEN}"}
        ).json()

        # 확정하지 않았으므로 전부 pending 이고 유효 링크는 0이어야 한다.
        assert body["transfers"]["pending"] == 3
        assert body["transfers"]["live"] == 0
        assert body["activity"]["uploads_today"] == 3


class TestTransferList:
    def _make_transfer(self, client: TestClient, name: str = "보고서.pdf") -> str:
        created = client.post(
            "/api/transfers",
            json={
                "files": [{"name": name, "size": 10, "mime": "application/pdf"}],
                "expires_in": 3600,
            },
        )
        assert created.status_code == 201
        return str(created.json()["code"])

    def test_requires_auth(self, admin_client: TestClient) -> None:
        assert admin_client.get("/api/admin/transfers").status_code == 401

    def test_lists_uploaded_transfers(self, admin_client: TestClient) -> None:
        code = self._make_transfer(admin_client)

        body = admin_client.get(
            "/api/admin/transfers", headers={"Authorization": f"Bearer {TOKEN}"}
        ).json()

        assert body["total"] == 1
        row = body["items"][0]
        assert row["code"] == code
        assert row["files"][0]["name"] == "보고서.pdf"
        assert row["status"] == "pending"

    def test_uploader_is_a_short_hash_not_an_ip(self, admin_client: TestClient) -> None:
        """반복 업로더는 식별하되 원본 IP 는 복원되지 않아야 한다."""
        self._make_transfer(admin_client)
        row = admin_client.get(
            "/api/admin/transfers", headers={"Authorization": f"Bearer {TOKEN}"}
        ).json()["items"][0]

        assert len(row["uploader"]) == 8
        assert "." not in row["uploader"]  # IPv4 형태가 아니다

    def test_never_exposes_owner_token(self, admin_client: TestClient) -> None:
        """소유자 토큰이 새면 누구나 남의 전송을 지울 수 있다."""
        self._make_transfer(admin_client)
        raw = admin_client.get(
            "/api/admin/transfers", headers={"Authorization": f"Bearer {TOKEN}"}
        ).text
        assert "owner_token" not in raw

    def test_newest_first(self, admin_client: TestClient) -> None:
        first = self._make_transfer(admin_client, "첫번째.txt")
        second = self._make_transfer(admin_client, "두번째.txt")

        items = admin_client.get(
            "/api/admin/transfers", headers={"Authorization": f"Bearer {TOKEN}"}
        ).json()["items"]
        codes_in_order = [row["code"] for row in items]
        assert codes_in_order.index(second) < codes_in_order.index(first)

    def test_status_filter(self, admin_client: TestClient) -> None:
        self._make_transfer(admin_client)
        body = admin_client.get(
            "/api/admin/transfers?status=ready", headers={"Authorization": f"Bearer {TOKEN}"}
        ).json()
        assert body["total"] == 0

    def test_rejects_unknown_status(self, admin_client: TestClient) -> None:
        response = admin_client.get(
            "/api/admin/transfers?status=nonsense",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        assert response.status_code == 400


class TestForceDelete:
    def test_requires_auth(self, admin_client: TestClient) -> None:
        assert admin_client.delete("/api/admin/transfers/oslo/123456").status_code == 401

    def test_deletes_without_owner_token(self, admin_client: TestClient) -> None:
        """신고 대응이므로 소유자 토큰 없이 지울 수 있어야 한다."""
        created = admin_client.post(
            "/api/transfers",
            json={"files": [{"name": "a.txt", "size": 5, "mime": ""}], "expires_in": 3600},
        ).json()
        code = created["code"]

        response = admin_client.delete(
            f"/api/admin/transfers/{code}", headers={"Authorization": f"Bearer {TOKEN}"}
        )
        assert response.status_code == 200
        assert admin_client.get(f"/api/transfers/{code}").status_code == 410

    def test_removes_files_from_disk(self, admin_client: TestClient, tmp_path: Path) -> None:
        """레코드만 지우면 파일이 디스크에 영원히 남는다 — 목록에도 안 보인다."""
        created = admin_client.post(
            "/api/transfers",
            json={"files": [{"name": "a.txt", "size": 5, "mime": ""}], "expires_in": 3600},
        ).json()
        key = created["uploads"][0]["key"]
        target = tmp_path / "files" / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"hello")
        assert target.exists()

        admin_client.delete(
            f"/api/admin/transfers/{created['code']}",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        assert not target.exists()

    def test_unknown_code_is_404(self, admin_client: TestClient) -> None:
        response = admin_client.delete(
            "/api/admin/transfers/oslo/999999", headers={"Authorization": f"Bearer {TOKEN}"}
        )
        assert response.status_code == 404
