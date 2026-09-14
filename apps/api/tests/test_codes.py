"""공유 코드 생성·검증과 단어 목록의 건전성."""

import re

import pytest

from app.services import codes
from app.wordlist import CITIES, NATURE, WORDS


class TestWordlist:
    def test_no_duplicates(self) -> None:
        """중복 단어는 코드 공간을 줄이면서 아무 이득이 없다."""
        assert len(WORDS) == len(set(WORDS)), (
            f"중복: {sorted({w for w in WORDS if list(WORDS).count(w) > 1})}"
        )

    def test_cities_and_nature_do_not_overlap(self) -> None:
        assert not (set(CITIES) & set(NATURE))

    def test_all_lowercase_ascii(self) -> None:
        """한국어 사용자가 자판 전환 없이 입력할 수 있어야 한다."""
        for word in WORDS:
            assert re.fullmatch(r"[a-z]{3,12}", word), f"부적합한 단어: {word!r}"

    def test_enough_words(self) -> None:
        """코드 공간이 좁으면 열거 공격이 현실적인 위협이 된다."""
        assert len(WORDS) >= 256


class TestGenerateCode:
    def test_format(self) -> None:
        for _ in range(200):
            code = codes.generate_code()
            assert re.fullmatch(r"[a-z]{3,12}/\d{6}", code)

    def test_number_never_has_leading_zero(self) -> None:
        """6자리를 유지해야 URL 패턴과 라우트 정규식이 어긋나지 않는다."""
        for _ in range(200):
            number = codes.generate_code().split("/")[1]
            assert len(number) == 6
            assert not number.startswith("0")

    def test_generated_codes_are_valid(self) -> None:
        for _ in range(100):
            assert codes.is_valid_code(codes.generate_code())

    def test_spread(self) -> None:
        """난수가 한쪽으로 쏠리지 않는지 대략 확인한다."""
        generated = {codes.generate_code() for _ in range(500)}
        assert len(generated) == 500  # 충돌이 나면 코드 공간 설계가 잘못된 것


class TestValidateCode:
    @pytest.mark.parametrize(
        "code",
        [
            "notaword/123456",  # 목록에 없는 단어
            "oslo/12345",  # 5자리
            "oslo/1234567",  # 7자리
            "OSLO/123456",  # 대문자
            "oslo/abcdef",
            "oslo",
            "oslo/123456/extra",
            "../../etc/passwd",
            "",
        ],
    )
    def test_rejects_malformed(self, code: str) -> None:
        assert not codes.is_valid_code(code)

    def test_accepts_known_word(self) -> None:
        assert codes.is_valid_code(f"{WORDS[0]}/123456")

    def test_normalize_lowercases_and_trims(self) -> None:
        assert codes.normalize_code(" OSLO ", " 113245 ") == "oslo/113245"


class TestOwnerToken:
    def test_unique_and_long_enough(self) -> None:
        tokens = {codes.generate_owner_token() for _ in range(500)}
        assert len(tokens) == 500
        assert all(len(t) >= 40 for t in tokens)
