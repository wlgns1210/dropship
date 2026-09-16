"""여러 파일을 ZIP 하나로 **흘려보내는** 생성기.

왜 이렇게 만드나
----------------
**압축하지 않는다(ZIP_STORED).** 이 서비스로 오가는 건 대부분 이미 압축된
파일(jpg, mp4, zip, pdf)이다. 다시 압축해도 크기는 거의 안 줄고 CPU 만 쓴다.
저장만 하면 헤더를 붙인 복사에 가까워 1GB 도 부담이 없다.

**메모리에 쌓지 않는다.** 전체를 만들어 놓고 보내면 1GB 전송 하나가 램을 통째로
먹는다. zipfile 이 쓰는 즉시 그만큼을 내보내고 버퍼를 비운다.

**되감기(seek)를 쓰지 않는다.** 응답 스트림은 뒤로 갈 수 없다. seek 메서드를
아예 두지 않으면 zipfile 이 그걸 감지해 data descriptor 방식으로 쓴다 —
크기와 CRC 를 미리 알 필요가 없는 형식이라 스트리밍에 맞는다.

이 경로에서는 파일 바이트가 Python 을 지나간다. 단일 파일 다운로드는 nginx 가
sendfile 로 직접 보내지만(X-Accel-Redirect), ZIP 은 여러 파일을 하나로 엮어야
해서 누군가는 바이트를 만져야 한다. 대신 압축을 하지 않아 비용을 최소화했다.
"""

import zipfile
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from typing import IO, cast

#: 한 번에 내보내는 최소 덩어리. 너무 작게 쪼개면 호출이 잦아 느려진다.
_FLUSH_THRESHOLD = 256 * 1024


class _Sink:
    """zipfile 이 쓴 바이트를 모아 두었다가 꺼내 주는 임시 버퍼.

    ``seek`` 을 일부러 정의하지 않는다. zipfile 이 AttributeError 를 보고
    "되감을 수 없는 스트림" 으로 판단해 data descriptor 를 쓰게 하기 위해서다.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._position = 0

    def write(self, data: bytes) -> int:
        self._buffer += data
        self._position += len(data)
        return len(data)

    def tell(self) -> int:
        return self._position

    def flush(self) -> None:
        pass

    def drain(self, *, force: bool = False) -> bytes | None:
        if not self._buffer:
            return None
        if not force and len(self._buffer) < _FLUSH_THRESHOLD:
            return None
        chunk = bytes(self._buffer)
        self._buffer.clear()
        return chunk


def unique_names(names: Iterable[str]) -> list[str]:
    """같은 이름이 겹치면 뒤에 번호를 붙인다.

    한 전송에 같은 파일명이 여러 개 들어올 수 있다(다른 폴더에서 고르면 흔하다).
    ZIP 안에 중복 이름이 있으면 압축 해제 프로그램마다 동작이 달라진다 —
    덮어쓰거나, 하나만 풀거나, 오류를 낸다.
    """
    seen: dict[str, int] = {}
    result: list[str] = []
    for name in names:
        if name not in seen:
            seen[name] = 1
            result.append(name)
            continue
        seen[name] += 1
        stem, dot, suffix = name.rpartition(".")
        if dot:
            result.append(f"{stem} ({seen[name]}).{suffix}")
        else:
            result.append(f"{name} ({seen[name]})")
    return result


def stream_zip(
    entries: Iterable[tuple[str, Iterator[bytes]]], *, created_at: int | None = None
) -> Iterator[bytes]:
    """``(파일명, 바이트 조각들)`` 을 받아 ZIP 바이트를 순서대로 내보낸다."""
    sink = _Sink()
    when = datetime.fromtimestamp(created_at or 0, UTC) if created_at else datetime.now(UTC)
    timestamp = (when.year, when.month, when.day, when.hour, when.minute, when.second)

    # cast 가 필요한 이유: zipfile 의 타입 스텁은 쓰기 대상에 seek/truncate 를
    # 요구한다. 우리는 **일부러** 그 메서드를 두지 않았다 — 없어야 zipfile 이
    # 되감을 수 없는 스트림으로 판단해 data descriptor 방식으로 쓴다.
    # 런타임 동작은 정확하고, 스텁이 스트리밍 쓰기를 표현하지 못할 뿐이다.
    with zipfile.ZipFile(
        cast("IO[bytes]", sink), "w", zipfile.ZIP_STORED, allowZip64=True
    ) as archive:
        for name, chunks in entries:
            info = zipfile.ZipInfo(name, date_time=timestamp)
            info.compress_type = zipfile.ZIP_STORED
            # 이름에 ASCII 가 아닌 글자가 있으면 zipfile 이 UTF-8 플래그를 켜준다.
            # 최신 Windows 탐색기는 이 플래그를 존중하므로 한글 파일명이 유지된다.
            with archive.open(info, "w") as target:
                for chunk in chunks:
                    target.write(chunk)
                    ready = sink.drain()
                    if ready:
                        yield ready
            ready = sink.drain()
            if ready:
                yield ready

    # 중앙 디렉터리까지 남김없이 내보낸다. 이게 빠지면 파일이 열리지 않는다.
    ready = sink.drain(force=True)
    if ready:
        yield ready
