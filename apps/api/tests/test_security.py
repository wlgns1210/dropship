"""파일명 처리와 IP 해시.

한국어 파일명을 제대로 다루는지가 이 서비스에서는 기능 요구사항이다.
"""

import unicodedata

import pytest

from app.services.security import hash_ip, is_risky, sanitize_filename
from app.services.storage import content_disposition


class TestSanitizeFilename:
    def test_keeps_korean(self) -> None:
        assert sanitize_filename("보고서 최종.pdf") == "보고서 최종.pdf"

    def test_keeps_emoji_and_spaces(self) -> None:
        assert sanitize_filename("여행 사진 🏖.jpg") == "여행 사진 🏖.jpg"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("../../etc/passwd", "passwd"),
            ("/etc/passwd", "passwd"),
            (r"C:\Users\me\secret.txt", "secret.txt"),
            (r"..\..\windows\system32\cmd.exe", "cmd.exe"),
            ("a/b/c/파일.txt", "파일.txt"),
        ],
    )
    def test_strips_path_traversal(self, raw: str, expected: str) -> None:
        assert sanitize_filename(raw) == expected

    def test_normalizes_nfd_to_nfc(self) -> None:
        """macOS 는 한글 파일명을 자모 분리(NFD)해서 보낸다.

        그대로 두면 목록에 "ㅎㅏㄴㄱㅡㄹ.txt" 처럼 풀어져 보인다.
        """
        nfd = unicodedata.normalize("NFD", "한글.txt")
        assert nfd != "한글.txt"  # 전제 확인
        assert sanitize_filename(nfd) == "한글.txt"

    def test_strips_control_characters(self) -> None:
        assert sanitize_filename("보고서\x00\n\t.pdf") == "보고서.pdf"

    def test_empty_falls_back(self) -> None:
        assert sanitize_filename("") == "파일"
        assert sanitize_filename("...") == "파일"
        assert sanitize_filename("/") == "파일"

    def test_truncates_long_names(self) -> None:
        assert len(sanitize_filename("가" * 500)) <= 255


class TestContentDisposition:
    def test_korean_filename_is_percent_encoded(self) -> None:
        header = content_disposition("보고서.pdf")
        assert header.startswith("attachment; filename*=UTF-8''")
        assert "%EB%B3%B4%EA%B3%A0%EC%84%9C" in header

    def test_header_is_ascii_safe(self) -> None:
        """HTTP 헤더에 non-ASCII 가 그대로 들어가면 서버·프록시가 거부한다."""
        content_disposition("여행 사진 🏖.jpg").encode("ascii")

    def test_quotes_and_semicolons_are_escaped(self) -> None:
        header = content_disposition('bad"; name="evil.exe')
        assert '"' not in header.split("''", 1)[1]


class TestIsRisky:
    @pytest.mark.parametrize("name", ["setup.exe", "run.BAT", "app.apk", "x.ps1"])
    def test_flags_executables(self, name: str) -> None:
        assert is_risky(name)

    @pytest.mark.parametrize("name", ["보고서.pdf", "사진.jpg", "data.csv", "noext"])
    def test_allows_ordinary_files(self, name: str) -> None:
        assert not is_risky(name)


class TestHashIp:
    def test_is_deterministic(self) -> None:
        assert hash_ip("1.2.3.4", "salt") == hash_ip("1.2.3.4", "salt")

    def test_salt_changes_output(self) -> None:
        assert hash_ip("1.2.3.4", "a") != hash_ip("1.2.3.4", "b")

    def test_does_not_contain_raw_ip(self) -> None:
        assert "1.2.3.4" not in hash_ip("1.2.3.4", "salt")
