"""클라이언트 IP 판별.

레이트리밋과 일일 쿼터가 전부 이 값 하나에 걸려 있다. 여기가 위조 가능하면
익명 서비스의 방어는 없는 것과 같으므로, 우회 시나리오를 테스트로 못 박는다.
"""

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.services.security import client_ip

probe = FastAPI()


@probe.get("/whoami")
def whoami(request: Request) -> dict[str, str]:
    """CloudFront 뒤에 있는 구성 (AWS 모드)."""
    return {"ip": client_ip(request, behind_cloudfront=True)}


@probe.get("/whoami-single")
def whoami_single(request: Request) -> dict[str, str]:
    """CloudFront 가 없는 구성 (단일 EC2). 전용 헤더를 믿으면 안 된다."""
    return {"ip": client_ip(request)}


@pytest.fixture
def probe_client() -> TestClient:
    return TestClient(probe)


class TestForgery:
    def test_forged_xff_does_not_win_over_cloudfront_header(
        self, probe_client: TestClient
    ) -> None:
        """CloudFront-Viewer-Address 가 있으면 XFF 는 무시된다.

        CloudFront 는 이 헤더를 자기가 채우고 뷰어가 보낸 동명 헤더를 덮어쓴다.
        """
        response = probe_client.get(
            "/whoami",
            headers={
                "cloudfront-viewer-address": "203.0.113.9:44321",
                "x-forwarded-for": "1.2.3.4",
            },
        )
        assert response.json()["ip"] == "203.0.113.9"

    def test_uses_last_xff_hop_not_first(self, probe_client: TestClient) -> None:
        """이것이 이 모듈의 핵심이다.

        CloudFront 는 뷰어가 보낸 XFF 를 지우지 않고 뒤에 실제 IP 를 덧붙인다.
        첫 번째 값을 쓰면 공격자가 ``X-Forwarded-For: <아무 값>`` 을 보내는 것만으로
        매 요청 다른 신원이 되어 레이트리밋과 쿼터를 통째로 우회한다.
        """
        response = probe_client.get(
            "/whoami",
            # 앞의 두 개는 공격자가 심은 값, 마지막이 CloudFront 가 붙인 실제 IP
            headers={"x-forwarded-for": "1.2.3.4, 5.6.7.8, 203.0.113.9"},
        )
        assert response.json()["ip"] == "203.0.113.9"

    def test_single_hop_xff(self, probe_client: TestClient) -> None:
        response = probe_client.get("/whoami", headers={"x-forwarded-for": "203.0.113.9"})
        assert response.json()["ip"] == "203.0.113.9"

    def test_falls_back_to_socket_when_no_proxy(self, probe_client: TestClient) -> None:
        assert probe_client.get("/whoami").json()["ip"] == "testclient"


class TestPortStripping:
    def test_ipv4_port_is_removed(self, probe_client: TestClient) -> None:
        response = probe_client.get(
            "/whoami", headers={"cloudfront-viewer-address": "203.0.113.9:44321"}
        )
        assert response.json()["ip"] == "203.0.113.9"

    def test_ipv6_keeps_all_but_last_colon_group(self, probe_client: TestClient) -> None:
        """IPv6 는 콜론이 여러 개다. 단순 split(":")[0] 이면 주소가 뭉개진다."""
        response = probe_client.get(
            "/whoami",
            headers={"cloudfront-viewer-address": "2001:db8:85a3::8a2e:370:7334:44321"},
        )
        assert response.json()["ip"] == "2001:db8:85a3::8a2e:370:7334"

    def test_address_without_port_is_left_alone(self, probe_client: TestClient) -> None:
        response = probe_client.get(
            "/whoami", headers={"cloudfront-viewer-address": "203.0.113.9"}
        )
        assert response.json()["ip"] == "203.0.113.9"


class TestQuotaIsolation:
    def test_different_ips_get_separate_quota_buckets(self, client: TestClient) -> None:
        """서로 다른 IP 는 쿼터가 섞이지 않아야 한다."""
        from app.config import get_settings
        from app.services.security import hash_ip

        salt = get_settings().ip_hash_salt
        assert hash_ip("203.0.113.9", salt) != hash_ip("203.0.113.10", salt)


class TestCloudFrontHeaderIsOnlyTrustedBehindCloudFront:
    """이 헤더는 CloudFront 가 덮어써 주기 때문에만 안전하다.

    CloudFront 가 없는 단일 EC2 에서는 nginx 가 클라이언트의 동명 헤더를 그대로
    통과시키므로, 믿으면 헤더 하나로 레이트리밋과 일일 쿼터가 통째로 우회된다.
    실제로 배포된 서버에서 재현됐던 취약점이다.
    """

    def test_single_node_ignores_the_header(self, probe_client: TestClient) -> None:
        response = probe_client.get(
            "/whoami-single",
            headers={
                "cloudfront-viewer-address": "203.0.113.9:44321",
                "x-forwarded-for": "198.51.100.1",
            },
        )
        # 위조된 CloudFront 헤더가 아니라 프록시가 붙인 XFF 를 써야 한다
        assert response.json()["ip"] == "198.51.100.1"

    def test_single_node_falls_back_to_socket(self, probe_client: TestClient) -> None:
        response = probe_client.get(
            "/whoami-single", headers={"cloudfront-viewer-address": "203.0.113.9:44321"}
        )
        assert response.json()["ip"] == "testclient"

    def test_forged_header_cannot_mint_new_identities(self, probe_client: TestClient) -> None:
        """매 요청 다른 값을 보내도 신원이 갈리지 않아야 한다."""
        seen = {
            probe_client.get(
                "/whoami-single",
                headers={"cloudfront-viewer-address": f"203.0.113.{i}:5000"},
            ).json()["ip"]
            for i in range(10)
        }
        assert len(seen) == 1

    def test_behind_cloudfront_still_uses_the_header(self, probe_client: TestClient) -> None:
        """AWS 모드에서는 여전히 이 헤더가 가장 믿을 만한 값이다."""
        response = probe_client.get(
            "/whoami",
            headers={
                "cloudfront-viewer-address": "203.0.113.9:44321",
                "x-forwarded-for": "1.2.3.4",
            },
        )
        assert response.json()["ip"] == "203.0.113.9"
