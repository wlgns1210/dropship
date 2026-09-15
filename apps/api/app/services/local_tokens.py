"""단일 EC2 모드에서 쓰는 URL 서명 토큰.

S3 presigned URL 이 하던 일을 대신한다: **시간이 제한되고 대상이 고정된
일회성 허가증**. 업로드 파트 URL 과 다운로드 URL 이 이걸 달고 나간다.

왜 직접 만드나
--------------
JWT 라이브러리를 새로 넣을 만한 일이 아니다. 필요한 것은
"짧은 JSON 에 HMAC 을 붙이고 만료를 본다" 뿐이고, 표준 라이브러리로 20줄이면
된다. 의존성이 하나 줄면 Lambda 패키지도 배포도 그만큼 단순해진다.

서명은 HMAC-SHA256 이고 비교는 ``compare_digest`` 로 한다. 일반 문자열 비교는
앞에서부터 다르면 즉시 끝나서, 응답 시간 차이로 서명을 한 바이트씩 맞춰갈 수
있기 때문이다.
"""

import base64
import hashlib
import hmac
import json
import time
from typing import Any


def _b64encode(raw: bytes) -> str:
    # URL 에 그대로 들어가야 하므로 패딩(=)을 떼고 urlsafe 알파벳을 쓴다.
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def sign(secret: str, payload: dict[str, Any], ttl: int) -> str:
    """``<본문>.<서명>`` 형태의 토큰을 만든다."""
    body = {**payload, "exp": int(time.time()) + ttl}
    encoded = _b64encode(json.dumps(body, separators=(",", ":"), sort_keys=True).encode())
    signature = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest()
    return f"{encoded}.{_b64encode(signature)}"


def verify(secret: str, token: str) -> dict[str, Any] | None:
    """유효하면 본문을, 아니면 None 을 돌려준다.

    실패 이유(서명 불일치/만료/형식 오류)를 구분해 알려주지 않는다. 호출부가
    어떤 경우든 같은 응답을 주게 해서 정보가 새지 않도록 하기 위해서다.
    """
    encoded, separator, provided = token.partition(".")
    if not separator:
        return None

    expected = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(_b64encode(expected), provided):
        return None

    try:
        body: dict[str, Any] = json.loads(_b64decode(encoded))
    except (ValueError, json.JSONDecodeError):
        return None

    if int(body.get("exp", 0)) <= int(time.time()):
        return None
    return body
