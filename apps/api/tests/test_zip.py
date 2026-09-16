"""ZIP 일괄 다운로드.

파일 개수 상한을 100개로 올린 뒤에는 개별 버튼만으로 받게 둘 수 없다.
받는 사람이 100번 눌러야 한다.
"""

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.services import zipstream


class TestUniqueNames:
    """ZIP 안에 같은 이름이 둘 있으면 압축 해제 프로그램마다 동작이 갈린다.

    덮어쓰거나, 하나만 풀거나, 오류를 낸다. 다른 폴더에서 같은 이름의 파일을
    고르는 일은 흔하므로 서버가 미리 갈라 줘야 한다.
    """

    def test_leaves_distinct_names_alone(self) -> None:
        assert zipstream.unique_names(["a.txt", "b.txt"]) == ["a.txt", "b.txt"]

    def test_numbers_duplicates(self) -> None:
        assert zipstream.unique_names(["a.txt", "a.txt", "a.txt"]) == [
            "a.txt",
            "a (2).txt",
            "a (3).txt",
        ]

    def test_keeps_extension_position(self) -> None:
        """번호는 확장자 앞에 붙어야 한다. 'a.txt (2)' 면 확장자가 사라진다."""
        assert zipstream.unique_names(["보고서.pdf", "보고서.pdf"])[1] == "보고서 (2).pdf"

    def test_handles_names_without_extension(self) -> None:
        assert zipstream.unique_names(["README", "README"]) == ["README", "README (2)"]


class TestStreamZip:
    def test_produces_a_readable_archive(self) -> None:
        entries = [
            ("첫번째.txt", iter([b"hello ", b"world"])),
            ("두번째.bin", iter([b"\x00\x01\x02"])),
        ]
        blob = b"".join(zipstream.stream_zip(entries))

        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            assert archive.namelist() == ["첫번째.txt", "두번째.bin"]
            assert archive.read("첫번째.txt") == b"hello world"
            assert archive.read("두번째.bin") == b"\x00\x01\x02"

    def test_archive_is_valid(self) -> None:
        """중앙 디렉터리가 빠지면 파일이 아예 안 열린다."""
        blob = b"".join(zipstream.stream_zip([("a.txt", iter([b"x" * 1000]))]))
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            assert archive.testzip() is None

    def test_is_stored_not_deflated(self) -> None:
        """이미 압축된 파일이 대부분이라 재압축은 CPU 만 쓴다."""
        blob = b"".join(zipstream.stream_zip([("a.txt", iter([b"x" * 5000]))]))
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            assert archive.infolist()[0].compress_type == zipfile.ZIP_STORED

    def test_korean_names_survive(self) -> None:
        blob = b"".join(zipstream.stream_zip([("보고서 최종 🏖.pdf", iter([b"x"]))]))
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            assert archive.namelist() == ["보고서 최종 🏖.pdf"]

    def test_streams_incrementally(self) -> None:
        """전체를 만들어 놓고 내보내면 1GB 전송이 램을 통째로 먹는다."""
        chunks = list(
            zipstream.stream_zip([("big.bin", iter([b"x" * (256 * 1024)] * 8))])
        )
        assert len(chunks) > 1, "한 덩어리로 나왔다면 스트리밍이 아니다"

    def test_empty_file(self) -> None:
        blob = b"".join(zipstream.stream_zip([("empty.txt", iter([]))]))
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            assert archive.read("empty.txt") == b""


class TestDownloadAllEndpoint:
    def _multi(self, client: TestClient, names: list[str]) -> str:
        """여러 파일짜리 전송을 만들고 확정까지 한다."""
        import boto3

        from tests.conftest import BUCKET, REGION

        created = client.post(
            "/api/transfers",
            json={
                "files": [{"name": n, "size": 5, "mime": "text/plain"} for n in names],
                "expires_in": 3600,
            },
        )
        assert created.status_code == 201
        session = created.json()

        s3 = boto3.client("s3", region_name=REGION)
        completed = []
        for upload in session["uploads"]:
            result = s3.upload_part(
                Bucket=BUCKET,
                Key=upload["key"],
                UploadId=upload["upload_id"],
                PartNumber=1,
                Body=f"body{upload['file_index']}".encode(),
            )
            completed.append(
                {
                    "file_index": upload["file_index"],
                    "parts": [{"part_number": 1, "etag": result["ETag"]}],
                }
            )

        done = client.post(
            f"/api/transfers/{session['code']}/complete",
            json={"owner_token": session["owner_token"], "files": completed},
        )
        assert done.status_code == 200
        return str(session["code"])

    def test_issues_a_token_url(self, client: TestClient) -> None:
        code = self._multi(client, ["첫번째.txt", "두번째.txt"])
        response = client.get(f"/api/transfers/{code}/download-all")

        assert response.status_code == 200
        assert response.json()["url"].startswith("/api/zip/")

    def test_single_file_transfer_is_rejected(self, client: TestClient) -> None:
        """한 개짜리를 ZIP 으로 감싸면 받는 사람만 번거로워진다."""
        code = self._multi(client, ["혼자.txt"])
        assert client.get(f"/api/transfers/{code}/download-all").status_code == 400

    def test_expired_transfer_has_no_zip(self, client: TestClient) -> None:
        import time

        import boto3

        from tests.conftest import REGION, TABLE

        code = self._multi(client, ["a.txt", "b.txt"])
        past = int(time.time()) - 60
        boto3.resource("dynamodb", region_name=REGION).Table(TABLE).update_item(
            Key={"PK": f"T#{code}", "SK": "META"},
            UpdateExpression="SET expires_at = :t, GSI1SK = :t",
            ExpressionAttributeValues={":t": past},
        )
        assert client.get(f"/api/transfers/{code}/download-all").status_code == 410

    @pytest.mark.parametrize("bad", ["fake.token", "a.b", ""])
    def test_forged_zip_token_is_rejected(self, client: TestClient, bad: str) -> None:
        response = client.get(f"/api/zip/{bad}")
        assert response.status_code in (403, 404)

    def test_streams_every_file(self, client: TestClient) -> None:
        code = self._multi(client, ["첫번째.txt", "두번째.txt", "세번째.txt"])
        url = client.get(f"/api/transfers/{code}/download-all").json()["url"]

        response = client.get(url)
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"

        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert archive.namelist() == ["첫번째.txt", "두번째.txt", "세번째.txt"]
            assert archive.read("첫번째.txt") == b"body0"
            assert archive.read("세번째.txt") == b"body2"

    def test_archive_filename_is_prefixed_without_the_word(self, client: TestClient) -> None:
        """dropship-<숫자>.zip

        접두사는 다운로드 폴더에서 출처를 알려준다. 코드의 앞 단어(도시명)는
        링크를 부르기 쉽게 하려고 붙인 것일 뿐 뜻이 없어서, 파일명에 남기면
        받는 사람에게는 정체 모를 단어로만 보인다.
        """
        from urllib.parse import unquote

        code = self._multi(client, ["a.txt", "b.txt"])
        word, number = code.split("/")
        url = client.get(f"/api/transfers/{code}/download-all").json()["url"]

        disposition = client.get(url).headers["content-disposition"]
        name = unquote(disposition.split("''")[-1])

        assert name == f"dropship-{number}.zip"
        assert word not in name

    def test_duplicate_names_are_separated_in_the_archive(self, client: TestClient) -> None:
        code = self._multi(client, ["같은이름.txt", "같은이름.txt"])
        url = client.get(f"/api/transfers/{code}/download-all").json()["url"]

        with zipfile.ZipFile(io.BytesIO(client.get(url).content)) as archive:
            assert archive.namelist() == ["같은이름.txt", "같은이름 (2).txt"]
