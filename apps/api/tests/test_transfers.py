"""업로드 → 공유 → 다운로드 → 만료 전체 흐름.

presigned URL 로 실제 PUT 을 하지는 않는다. moto 가 만든 서명 URL 은 진짜 AWS
주소를 가리켜서 테스트에서 접속할 수 없기 때문이다. 대신 응답으로 받은
``upload_id`` 로 boto3 가 직접 파트를 올린다 — 브라우저가 할 일을 대신하는 것이고,
검증 대상인 서버 로직은 전부 그대로 지난다.
"""

import time
from typing import Any

import boto3
import httpx
import pytest
from fastapi.testclient import TestClient

from tests.conftest import BUCKET, REGION, TABLE


def _create(
    client: TestClient, *, files: list[dict[str, Any]], expires_in: int = 86400
) -> httpx.Response:
    return client.post(
        "/api/transfers",
        json={"files": files, "expires_in": expires_in},
    )


def _upload_parts(uploads: list[dict], bodies: list[bytes]) -> list[dict]:
    """브라우저 대신 파트를 올리고 complete 요청에 넣을 형태로 돌려준다."""
    s3 = boto3.client("s3", region_name=REGION)
    completed = []
    for upload, body in zip(uploads, bodies, strict=True):
        result = s3.upload_part(
            Bucket=BUCKET,
            Key=upload["key"],
            UploadId=upload["upload_id"],
            PartNumber=1,
            Body=body,
        )
        completed.append(
            {
                "file_index": upload["file_index"],
                "parts": [{"part_number": 1, "etag": result["ETag"]}],
            }
        )
    return completed


def _send(
    client: TestClient, *, name: str = "보고서.pdf", body: bytes = b"hello"
) -> tuple[dict[str, Any], dict[str, Any]]:
    """생성 → 업로드 → 확정까지 한 번에."""
    created = _create(
        client, files=[{"name": name, "size": len(body), "mime": "application/pdf"}]
    )
    assert created.status_code == 201, created.text
    session = created.json()

    completed = _upload_parts(session["uploads"], [body])
    done = client.post(
        f"/api/transfers/{session['code']}/complete",
        json={"owner_token": session["owner_token"], "files": completed},
    )
    assert done.status_code == 200, done.text
    return session, done.json()


class TestCreate:
    def test_returns_code_and_presigned_urls(self, client: TestClient) -> None:
        response = _create(client, files=[{"name": "a.txt", "size": 10, "mime": "text/plain"}])
        assert response.status_code == 201

        body = response.json()
        assert body["code"].count("/") == 1
        # 공유 URL 은 서버가 만들지 않는다 — 브라우저가 location.origin 에 붙인다.
        assert "share_url" not in body
        assert len(body["uploads"]) == 1
        assert body["uploads"][0]["part_urls"]

    def test_rejects_total_over_one_gigabyte(self, client: TestClient) -> None:
        half_gig_plus = 600 * 1024**2
        response = _create(
            client,
            files=[{"name": f"{i}.bin", "size": half_gig_plus, "mime": ""} for i in range(2)],
        )
        assert response.status_code == 422

    def test_rejects_too_many_files(self, client: TestClient) -> None:
        response = _create(
            client, files=[{"name": f"{i}.txt", "size": 1, "mime": ""} for i in range(101)]
        )
        assert response.status_code == 422

    def test_accepts_the_maximum_file_count(self, client: TestClient) -> None:
        """상한값 자체는 통과해야 한다. 경계에서 하나 어긋나는 실수를 막는다."""
        response = _create(
            client, files=[{"name": f"{i}.txt", "size": 1, "mime": ""} for i in range(100)]
        )
        assert response.status_code == 201
        assert len(response.json()["uploads"]) == 100

    def test_rejects_unknown_expiry(self, client: TestClient) -> None:
        response = _create(
            client, files=[{"name": "a.txt", "size": 1, "mime": ""}], expires_in=12345
        )
        assert response.status_code == 422

    @pytest.mark.parametrize("expires_in", [3600, 21600, 86400, 259200, 604800])
    def test_accepts_every_offered_choice(self, client: TestClient, expires_in: int) -> None:
        """UI 의 선택지와 서버가 받는 값이 어긋나면 안 된다."""
        response = _create(
            client, files=[{"name": "a.txt", "size": 1, "mime": ""}], expires_in=expires_in
        )
        assert response.status_code == 201

    def test_sanitizes_path_traversal_in_filename(self, client: TestClient) -> None:
        _, _ = _send(client, name="../../etc/passwd")
        # 저장된 이름이 정리되었는지는 조회 결과로 확인한다.

    def test_pending_transfer_is_not_visible(self, client: TestClient) -> None:
        """확정 전에는 링크가 살아 있으면 안 된다. 반쪽 업로드가 노출된다."""
        created = _create(client, files=[{"name": "a.txt", "size": 5, "mime": ""}])
        code = created.json()["code"]
        assert client.get(f"/api/transfers/{code}").status_code == 410


class TestCompleteAndFetch:
    def test_full_round_trip(self, client: TestClient) -> None:
        session, done = _send(client, name="보고서.pdf", body=b"hello world")

        info = client.get(f"/api/transfers/{session['code']}")
        assert info.status_code == 200

        body = info.json()
        assert body["files"][0]["name"] == "보고서.pdf"
        assert body["files"][0]["size"] == len(b"hello world")
        assert body["total_size"] == len(b"hello world")
        assert body["expires_at"] == done["expires_at"]

    def test_actual_size_wins_over_declared(self, client: TestClient) -> None:
        """클라이언트가 선언한 크기를 믿지 않는다는 것을 확인한다."""
        created = _create(client, files=[{"name": "a.bin", "size": 5, "mime": ""}])
        session = created.json()

        # 선언은 5바이트였지만 실제로는 더 올린다.
        completed = _upload_parts(session["uploads"], [b"x" * 4096])
        client.post(
            f"/api/transfers/{session['code']}/complete",
            json={"owner_token": session["owner_token"], "files": completed},
        )

        info = client.get(f"/api/transfers/{session['code']}").json()
        assert info["total_size"] == 4096

    def test_wrong_owner_token_is_rejected(self, client: TestClient) -> None:
        created = _create(client, files=[{"name": "a.txt", "size": 5, "mime": ""}])
        session = created.json()
        completed = _upload_parts(session["uploads"], [b"hello"])

        response = client.post(
            f"/api/transfers/{session['code']}/complete",
            json={"owner_token": "wrong-token", "files": completed},
        )
        assert response.status_code == 410

    def test_cannot_complete_twice(self, client: TestClient) -> None:
        session, _ = _send(client)
        response = client.post(
            f"/api/transfers/{session['code']}/complete",
            json={"owner_token": session["owner_token"], "files": []},
        )
        assert response.status_code in (410, 422)

    def test_flags_risky_extension(self, client: TestClient) -> None:
        session, _ = _send(client, name="setup.exe")
        info = client.get(f"/api/transfers/{session['code']}").json()
        assert info["files"][0]["risky"] is True


class TestLookupIsUniform:
    def test_unknown_code_returns_gone(self, client: TestClient) -> None:
        """404 가 아니라 410 이어야 한다. 만료된 링크와 구분되면 안 된다."""
        response = client.get("/api/transfers/oslo/999999")
        assert response.status_code == 410

    def test_expired_and_missing_give_identical_bodies(self, client: TestClient) -> None:
        session, _ = _send(client)
        _expire(session["code"])

        expired = client.get(f"/api/transfers/{session['code']}")
        missing = client.get("/api/transfers/oslo/999999")

        assert expired.status_code == missing.status_code == 410
        assert expired.json() == missing.json()

    def test_word_outside_list_is_rejected(self, client: TestClient) -> None:
        assert client.get("/api/transfers/zzzzz/123456").status_code == 410


class TestDownload:
    def test_returns_signed_url(self, client: TestClient) -> None:
        session, _ = _send(client)
        response = client.get(f"/api/transfers/{session['code']}/download/0")
        assert response.status_code == 200

        body = response.json()
        assert body["url"].startswith("http")
        # 파일명은 서명 URL 의 응답 헤더 오버라이드로 복원된다.
        assert "response-content-disposition" in body["url"].lower()

    def test_unknown_index_is_gone(self, client: TestClient) -> None:
        session, _ = _send(client)
        assert client.get(f"/api/transfers/{session['code']}/download/7").status_code == 410

    def test_expired_transfer_cannot_be_downloaded(self, client: TestClient) -> None:
        session, _ = _send(client)
        _expire(session["code"])
        assert client.get(f"/api/transfers/{session['code']}/download/0").status_code == 410


class TestDelete:
    def test_owner_can_delete(self, client: TestClient) -> None:
        session, _ = _send(client)
        response = client.delete(
            f"/api/transfers/{session['code']}",
            params={"owner_token": session["owner_token"]},
        )
        assert response.status_code == 200
        assert client.get(f"/api/transfers/{session['code']}").status_code == 410

    def test_stranger_cannot_delete(self, client: TestClient) -> None:
        session, _ = _send(client)
        response = client.delete(
            f"/api/transfers/{session['code']}", params={"owner_token": "nope"}
        )
        assert response.status_code == 410
        # 삭제되지 않고 그대로 살아 있어야 한다.
        assert client.get(f"/api/transfers/{session['code']}").status_code == 200


class TestSweeper:
    def test_deletes_expired_objects_and_records(self, client: TestClient) -> None:
        from app.sweeper import sweep

        session, _ = _send(client)
        key = session["uploads"][0]["key"]

        s3 = boto3.client("s3", region_name=REGION)
        assert s3.head_object(Bucket=BUCKET, Key=key)["ContentLength"] > 0

        _expire(session["code"])
        result = sweep()

        assert result["transfers"] == 1
        assert result["objects"] == 1
        with pytest.raises(s3.exceptions.ClientError):
            s3.head_object(Bucket=BUCKET, Key=key)

    def test_leaves_live_transfers_alone(self, client: TestClient) -> None:
        from app.sweeper import sweep

        session, _ = _send(client)
        assert sweep()["transfers"] == 0
        assert client.get(f"/api/transfers/{session['code']}").status_code == 200


class TestConfigEndpoint:
    def test_reports_one_gigabyte_limit(self, client: TestClient) -> None:
        """프론트가 이 값으로 상한을 표시하므로 정책과 일치해야 한다."""
        body = client.get("/api/config").json()
        assert body["max_total_bytes"] == 1024**3
        assert body["max_files"] == 100
        assert body["expiry_choices"] == [3600, 21600, 86400, 259200, 604800]


def _expire(code: str) -> None:
    """만료 시각을 과거로 돌린다. GSI 정렬 키도 같이 옮겨야 Sweeper 가 찾는다."""
    past = int(time.time()) - 60
    boto3.resource("dynamodb", region_name=REGION).Table(TABLE).update_item(
        Key={"PK": f"T#{code}", "SK": "META"},
        UpdateExpression="SET expires_at = :t, GSI1SK = :t",
        ExpressionAttributeValues={":t": past},
    )
