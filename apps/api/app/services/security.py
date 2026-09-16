"""익명 서비스를 지키는 잡다한 것들 — IP 취급, 파일명 정리, 응답 시간 평탄화."""

import asyncio
import hashlib
import time
import unicodedata
from pathlib import PurePosixPath, PureWindowsPath

from fastapi import Request

from app.config import MAX_FILENAME_LEN, RISKY_EXTENSIONS

#: 조회 계열 응답이 최소한 이만큼은 걸리게 만든다 (초).
#: 존재하는 코드와 없는 코드의 응답 시간 차이로 유효한 코드를 골라내는
#: 타이밍 공격을 막기 위한 것이다.
_MIN_LOOKUP_SECONDS = 0.08


def client_ip(request: Request, *, behind_cloudfront: bool = False) -> str:
    """레이트리밋·쿼터의 기준이 될, **위조할 수 없는** 클라이언트 IP.

    여기를 틀리면 익명 서비스의 방어가 통째로 무너지므로 순서가 중요하다.

    1. ``CloudFront-Viewer-Address`` — **CloudFront 뒤에 있을 때만** 본다.
       CloudFront 가 이 헤더를 직접 채우고 뷰어가 보낸 동명 헤더를 덮어쓰기
       때문에 그 구성에서는 위조할 수 없다.

       그러나 단일 EC2 처럼 CloudFront 가 없는 구성에서는 nginx 가 이 헤더를
       그대로 통과시키므로, 클라이언트가 직접 채워 보내면 그대로 믿게 된다.
       실제로 이 헤더 하나로 레이트리밋과 일일 쿼터가 통째로 우회됐다.
       그래서 배포 형태를 인자로 받아, 믿을 수 있는 구성에서만 읽는다.

    2. ``X-Forwarded-For`` 의 **마지막** 값 — 첫 번째가 아니다. CloudFront 는
       뷰어가 보낸 XFF 를 지우지 않고 뒤에 실제 IP 를 덧붙인다. 그래서 첫 번째
       값을 쓰면 공격자가 ``X-Forwarded-For: 1.2.3.4`` 를 보내는 것만으로 매
       요청마다 다른 신원을 꾸며내 레이트리밋과 일일 쿼터를 무한히 우회한다.
       가장 가까운 신뢰 프록시가 덧붙인 마지막 값만이 믿을 수 있다.

    3. 소켓 주소 — 프록시가 없는 로컬 개발용.

    이 함수가 신뢰할 수 있으려면 Lambda 함수 URL 이 CloudFront 를 거치지 않고는
    호출될 수 없어야 한다. 그 통제는 ``app.main`` 의 오리진 시크릿 검사가 한다.
    """
    if behind_cloudfront:
        viewer = request.headers.get("cloudfront-viewer-address")
        if viewer:
            return _strip_port(viewer.strip())

    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
        if hops:
            return hops[-1]

    return request.client.host if request.client else "0.0.0.0"


def _strip_port(address: str) -> str:
    """``1.2.3.4:56789`` 또는 ``2001:db8::1:56789`` 에서 포트를 떼어낸다.

    CloudFront-Viewer-Address 는 항상 포트를 붙여 준다. IPv6 는 콜론이 여러 개라
    마지막 콜론 기준으로만 잘라야 한다.
    """
    head, separator, _ = address.rpartition(":")
    return head if separator else address


def hash_ip(ip: str, salt: str) -> str:
    """원본 IP는 어디에도 저장하지 않는다. 쿼터·레이트리밋 키로만 쓰는 해시.

    솔트가 있어야 하는 이유: IPv4 는 공간이 43억뿐이라 솔트 없는 해시는
    전수 조사로 즉시 역산된다.
    """
    return hashlib.sha256(f"{salt}:{ip}".encode()).hexdigest()


def sanitize_filename(name: str) -> str:
    """경로 조작만 제거하고 **한글은 그대로 살린다.**

    파일명이 S3 키에 들어가지 않으므로(키는 ``<transfer_id>/<index>``) 여기서
    할 일은 표시용·다운로드 헤더용으로 안전하게 만드는 것뿐이다.
    """
    # 윈도우/POSIX 양쪽 구분자를 모두 벗겨낸다. 브라우저가 보내는 값에
    # "C:\\Users\\..." 나 "../../etc/passwd" 가 들어올 수 있다.
    stripped = PureWindowsPath(PurePosixPath(name).name).name

    # NFC 로 통일한다. macOS 는 한글을 자모 분리(NFD)해서 보내기 때문에
    # 이걸 안 하면 "한글.txt" 가 "ㅎㅏㄴㄱㅡㄹ.txt" 처럼 보인다.
    normalized = unicodedata.normalize("NFC", stripped)

    # 제어문자 제거
    cleaned = "".join(ch for ch in normalized if unicodedata.category(ch)[0] != "C")
    cleaned = cleaned.strip(" .")

    if not cleaned:
        return "파일"
    return cleaned[:MAX_FILENAME_LEN]


def is_risky(filename: str) -> bool:
    """실행 파일류인지. 차단하지는 않고 수신 화면에서 경고만 띄운다."""
    suffix = PurePosixPath(filename.lower()).suffix
    return suffix in RISKY_EXTENSIONS


async def flatten_timing(started_at: float) -> None:
    """조회 응답 시간을 하한선까지 끌어올려 타이밍 차이를 지운다."""
    elapsed = time.perf_counter() - started_at
    if elapsed < _MIN_LOOKUP_SECONDS:
        await asyncio.sleep(_MIN_LOOKUP_SECONDS - elapsed)
