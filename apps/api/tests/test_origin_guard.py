"""CloudFront 오리진 시크릿 검사.

Lambda 함수 URL 은 공개 주소다. 이 검사가 없으면 주소를 알아낸 사람이
CloudFront 를 건너뛰고 직접 호출할 수 있고, 그 경우 X-Forwarded-For 를
마음대로 꾸며 레이트리밋과 쿼터를 통째로 우회할 수 있다.
"""

import importlib
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import ORIGIN_SECRET_HEADER

SECRET = "test-origin-secret-value"


@pytest.fixture
def guarded_client(aws: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """오리진 시크릿이 켜진 상태의 앱을 새로 만든다.

    시크릿을 환경 변수로 넣는 이유: ``Settings.model_fields[...].default`` 를 직접
    바꿔봐야 pydantic 이 이미 만들어 둔 검증 스키마는 그대로라 반영되지 않는다.
    환경 변수로 넣으면 운영에서 실제로 값이 들어오는 경로를 그대로 지난다.

    미들웨어는 모듈 임포트 시점에 조건부로 등록되므로 ``app.main`` 을 다시 읽어야
    한다.
    """
    import app.config
    import app.main
    from app import deps

    monkeypatch.setenv("ORIGIN_SECRET", SECRET)
    app.config.get_settings.cache_clear()
    deps.get_storage.cache_clear()
    deps.get_repository.cache_clear()
    deps.get_signer.cache_clear()
    importlib.reload(app.main)

    try:
        with TestClient(app.main.app) as client:
            yield client
    finally:
        # 지우면 .env 값이 드러난다. conftest 기본값으로 되돌린다.
        monkeypatch.setenv("ORIGIN_SECRET", "")
        app.config.get_settings.cache_clear()
        importlib.reload(app.main)
        deps.get_storage.cache_clear()
        deps.get_repository.cache_clear()
        deps.get_signer.cache_clear()


class TestOriginGuard:
    def test_request_without_secret_is_forbidden(self, guarded_client: TestClient) -> None:
        assert guarded_client.get("/api/health").status_code == 403

    def test_wrong_secret_is_forbidden(self, guarded_client: TestClient) -> None:
        response = guarded_client.get(
            "/api/health", headers={ORIGIN_SECRET_HEADER: "wrong-value"}
        )
        assert response.status_code == 403

    def test_correct_secret_passes(self, guarded_client: TestClient) -> None:
        response = guarded_client.get("/api/health", headers={ORIGIN_SECRET_HEADER: SECRET})
        assert response.status_code == 200

    def test_guard_covers_upload_endpoint(self, guarded_client: TestClient) -> None:
        """헬스체크만이 아니라 실제 비용이 드는 경로도 막혀야 한다."""
        response = guarded_client.post(
            "/api/transfers",
            json={"files": [{"name": "a.txt", "size": 1, "mime": ""}], "expires_in": 3600},
        )
        assert response.status_code == 403


class TestGuardDisabledLocally:
    def test_no_secret_means_no_check(self, client: TestClient) -> None:
        """로컬 개발에서는 시크릿이 비어 있어 검사가 꺼진다."""
        assert client.get("/api/health").status_code == 200
