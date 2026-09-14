"""공유 코드 생성·검증.

코드 형식은 ``<단어>/<6자리 숫자>`` 다 (예: ``oslo/113245``).
전화로 불러주거나 손으로 옮겨 적기 쉬운 형태를 의도했다.

보안 주의
---------
코드 공간은 ``len(WORDS) × 900,000`` 이다. 이것은 **추측 불가능한 수준이 아니다.**
공유 링크를 지키는 것은 코드 길이가 아니라 조회 엔드포인트의 레이트리밋이다
(``app.services.ratelimit`` 과 운영 환경의 WAF 룰). 코드만 믿고
민감한 파일을 올리게 해서는 안 되며, 그래서 수신 화면에 고지를 띄운다.
"""

import re
import secrets

from app.wordlist import WORDS

_NUMBER_MIN = 100_000
_NUMBER_MAX = 999_999
_NUMBER_SPAN = _NUMBER_MAX - _NUMBER_MIN + 1

#: 코드 총 경우의 수. 레이트리밋 파라미터를 정할 때 근거로 쓴다.
CODE_SPACE = len(WORDS) * _NUMBER_SPAN

_CODE_RE = re.compile(r"^([a-z]{3,12})/([0-9]{6})$")


def generate_code() -> str:
    """암호학적 난수로 코드 1개를 만든다."""
    word = secrets.choice(WORDS)
    number = _NUMBER_MIN + secrets.randbelow(_NUMBER_SPAN)
    return f"{word}/{number}"


def is_valid_code(code: str) -> bool:
    """형식만 본다. 존재 여부는 저장소가 판단한다.

    형식 검증을 먼저 하는 이유는 명백한 쓰레기 요청이 DynamoDB 까지
    내려가지 않게 해서 열거 시도의 비용을 공격자 쪽에 남기기 위해서다.
    """
    match = _CODE_RE.match(code)
    if match is None:
        return False
    return match.group(1) in _WORD_SET


def normalize_code(word: str, number: str) -> str:
    """URL 경로 조각 두 개를 정규화된 코드 문자열로 합친다."""
    return f"{word.strip().lower()}/{number.strip()}"


def generate_owner_token() -> str:
    """업로드 확정·삭제 권한을 증명하는 토큰.

    익명 서비스라 계정으로 소유권을 확인할 수 없다. 업로더에게만 1회 건네고
    이후 요청에서 제시하게 한다. 256비트라 추측은 불가능하다.
    """
    return secrets.token_urlsafe(32)


_WORD_SET = frozenset(WORDS)
