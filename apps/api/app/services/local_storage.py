"""단일 EC2 모드의 파일 저장소. ``S3Storage`` 와 같은 인터페이스를 가진다.

S3 와의 대응
------------
=======================  ==================================================
S3                       여기
=======================  ==================================================
멀티파트 업로드 시작      파트 디렉터리를 만들고 파트별 서명 URL 을 발급
파트 presigned PUT       ``/api/transfers/parts/{token}`` 로 우리 서버가 받음
CompleteMultipartUpload  파트 파일들을 번호순으로 이어붙임
presigned GET            ``/api/d/{token}`` → nginx X-Accel-Redirect
=======================  ==================================================

**파일 바이트는 여기서도 Python 을 지나지 않는다** — 적어도 내려보낼 때는.
다운로드는 FastAPI 가 토큰만 확인하고 ``X-Accel-Redirect`` 헤더를 돌려주면
nginx 가 sendfile 로 직접 보낸다. 업로드는 어쩔 수 없이 지나가지만, 스트리밍
으로 받아 디스크에 바로 쓰기 때문에 파일 크기와 메모리 사용량이 무관하다.
"""

import os
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from app.config import PART_SIZE, Settings
from app.services import local_tokens
from app.services.storage import MultipartTarget, content_disposition, part_count_for


@dataclass(frozen=True)
class PartTarget:
    """파트 업로드 토큰이 허가하는 범위."""

    key: str
    part_number: int
    #: 이 파트가 받을 수 있는 최대 바이트. 세션 생성 시 선언한 크기에서 나온다.
    max_bytes: int


@dataclass(frozen=True)
class ResolvedFile:
    """토큰이 가리키는 실제 파일."""

    path: Path
    internal_url: str
    filename: str
    mime: str


class LocalStorage:
    def __init__(self, settings: Settings) -> None:
        self._root = settings.files_dir
        self._secret = settings.signing_key
        self._internal_prefix = settings.internal_files_prefix.rstrip("/")
        self._root.mkdir(parents=True, exist_ok=True)

    # ── 경로 ──────────────────────────────────────────────────

    def _object_path(self, key: str) -> Path:
        """키를 실제 경로로 바꾼다.

        키는 우리가 만든 ``<transfer_id>/<index>`` 뿐이지만, 경로 탈출은
        한 번이라도 뚫리면 서버 전체가 열리므로 방어적으로 확인한다.
        """
        candidate = (self._root / key).resolve()
        if not candidate.is_relative_to(self._root.resolve()):
            raise ValueError(f"저장소 밖을 가리키는 키: {key!r}")
        return candidate

    def _parts_dir(self, key: str) -> Path:
        return self._object_path(key + ".parts")

    def free_bytes(self) -> int | None:
        """저장소가 놓인 파일시스템의 남은 공간. 조회 실패는 None."""
        try:
            return shutil.disk_usage(self._root).free
        except OSError:
            return None

    # ── 업로드 ────────────────────────────────────────────────

    def start_multipart(
        self, key: str, mime: str, size: int, expires_in: int, filename: str
    ) -> MultipartTarget:
        parts_dir = self._parts_dir(key)
        parts_dir.mkdir(parents=True, exist_ok=True)

        url_ttl = min(expires_in, 12 * 3600)

        # 토큰에 **그 파트가 받을 수 있는 최대 바이트**를 함께 서명한다.
        #
        # 이게 없으면 "1바이트짜리 파일"이라고 신고해 쿼터를 1바이트만 쓰고,
        # 발급받은 파트 URL 로 8MB 를 밀어넣을 수 있다. complete 를 부르지
        # 않으면 pending 상태로 디스크에 그대로 남는다. 실제로 재현됐다.
        #
        # 크기가 토큰 안에 서명되어 있으므로 클라이언트가 고칠 수 없고,
        # 업로드된 실제 양은 항상 쿼터에 반영된 선언량 이하가 된다.
        def part_bytes(number: int) -> int:
            return max(0, min(PART_SIZE, size - (number - 1) * PART_SIZE))

        # 경로를 /api/transfers/ 아래 두지 않는다. 그 아래는 ``/{word}/{number}``
        # 패턴이 잡고 있어서 라우트 우선순위를 따져야 하는데, 별도 접두사를 쓰면
        # 그런 미묘함이 아예 생기지 않는다.
        part_urls = [
            "/api/upload/"
            + local_tokens.sign(
                self._secret,
                {"k": key, "p": number, "n": filename, "m": mime, "s": part_bytes(number)},
                url_ttl,
            )
            for number in range(1, part_count_for(size) + 1)
        ]
        # upload_id 가 따로 필요 없다. 파트 디렉터리가 그 역할을 한다.
        return MultipartTarget(
            key=key, upload_id="local", part_size=PART_SIZE, part_urls=part_urls
        )

    def resolve_part_token(self, token: str) -> PartTarget | None:
        """파트 업로드 토큰을 검증하고 대상과 크기 한도를 돌려준다."""
        body = local_tokens.verify(self._secret, token)
        if body is None or "k" not in body or "p" not in body:
            return None
        return PartTarget(
            key=str(body["k"]),
            part_number=int(body["p"]),
            # 크기가 없는 토큰은 이 방어가 들어오기 전에 발급된 것이다.
            # 남은 유효기간(최대 12시간) 동안만 존재하며, 그때는 전역 상한을 쓴다.
            max_bytes=int(body["s"]) if "s" in body else PART_SIZE,
        )

    def part_path(self, key: str, part_number: int) -> Path:
        return self._parts_dir(key) / f"{part_number:05d}"

    def finish_multipart(self, key: str, upload_id: str, parts: list[dict]) -> None:
        """파트 파일을 번호순으로 이어붙여 최종 파일을 만든다.

        임시 파일에 쓴 뒤 ``os.replace`` 로 옮긴다. 이어붙이는 도중에 프로세스가
        죽어도 반쪽짜리 파일이 최종 경로에 남지 않게 하기 위해서다
        (같은 파일시스템 안에서 rename 은 원자적이다).
        """
        destination = self._object_path(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".assembling")

        numbers = sorted(int(p["PartNumber"]) for p in parts)
        with temporary.open("wb") as output:
            for number in numbers:
                source = self.part_path(key, number)
                if not source.exists():
                    temporary.unlink(missing_ok=True)
                    raise FileNotFoundError(f"파트 {number} 이(가) 없습니다: {key}")
                with source.open("rb") as chunk:
                    # copyfileobj 는 고정 버퍼로 옮긴다. 파일 전체를 메모리에
                    # 올리지 않으므로 1GB 짜리도 안전하다.
                    shutil.copyfileobj(chunk, output, length=1024 * 1024)

        os.replace(temporary, destination)
        shutil.rmtree(self._parts_dir(key), ignore_errors=True)

    def abort_multipart(self, key: str, upload_id: str) -> None:
        shutil.rmtree(self._parts_dir(key), ignore_errors=True)

    # ── 검증 · 삭제 ───────────────────────────────────────────

    def object_size(self, key: str) -> int | None:
        try:
            return self._object_path(key).stat().st_size
        except (OSError, ValueError):
            return None

    def open_stream(self, key: str, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        """파일을 조각내어 읽는다. ZIP 일괄 다운로드가 쓴다.

        단일 파일 다운로드는 이 경로를 타지 않는다 — nginx 가 sendfile 로 직접
        보낸다. 여러 파일을 하나로 엮을 때만 어쩔 수 없이 여기를 지난다.
        """
        path = self._object_path(key)
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(chunk_size)
                if not chunk:
                    return
                yield chunk

    def delete_objects(self, keys: list[str]) -> None:
        for key in keys:
            try:
                path = self._object_path(key)
            except ValueError:
                continue
            path.unlink(missing_ok=True)
            shutil.rmtree(self._parts_dir(key), ignore_errors=True)
            # transfer_id 디렉터리가 비었으면 같이 치운다.
            parent = path.parent
            if parent != self._root and parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()

    # ── 다운로드 ──────────────────────────────────────────────

    def presign_download(self, key: str, filename: str, mime: str, ttl: int) -> str:
        token = local_tokens.sign(
            self._secret, {"k": key, "n": filename, "m": mime}, ttl
        )
        return f"/api/d/{token}"

    def resolve_download_token(self, token: str) -> ResolvedFile | None:
        """다운로드 토큰을 검증하고 nginx 에 넘길 내부 경로를 만든다."""
        body = local_tokens.verify(self._secret, token)
        if body is None or "k" not in body:
            return None

        key = str(body["k"])
        try:
            path = self._object_path(key)
        except ValueError:
            return None
        if not path.is_file():
            return None

        return ResolvedFile(
            path=path,
            internal_url=f"{self._internal_prefix}/{key}",
            filename=str(body.get("n") or "file"),
            mime=str(body.get("m") or "application/octet-stream"),
        )

    @staticmethod
    def download_headers(resolved: ResolvedFile) -> dict[str, str]:
        """nginx 가 파일을 직접 보내게 하는 헤더.

        X-Accel-Redirect 를 받으면 nginx 는 본문을 무시하고 internal location 의
        파일을 sendfile 로 보낸다. 덕분에 1GB 파일을 내려줘도 Python 프로세스는
        바이트를 하나도 만지지 않는다.
        """
        return {
            "X-Accel-Redirect": resolved.internal_url,
            "Content-Type": resolved.mime,
            "Content-Disposition": content_disposition(resolved.filename),
        }
