"""환경 설정과 서비스 전역 상수."""

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _env_files() -> tuple[Path, ...]:
    """읽어들일 ``.env`` 후보를 찾는다. 없으면 빈 튜플.

    위로 거슬러 올라가며 찾는다. 예전에는 ``parents[3]`` 으로 고정 깊이를
    가정했는데, 저장소 구조가 없는 곳에서는 그 깊이 자체가 존재하지 않는다.
    컨테이너에서 ``/app/app/config.py`` 로 놓이자 IndexError 가 나며 앱이
    기동조차 못 했다 — 설정을 읽기도 전에 죽으니 원인도 안 보였다.

    파일이 하나도 없어도 정상이다. 컨테이너나 systemd 배포에서는 환경 변수로
    값이 들어오고 ``.env`` 는 개발 편의 수단일 뿐이다.
    """
    found: list[Path] = []
    for parent in Path(__file__).resolve().parents:
        for name in (".env", ".env.example"):
            candidate = parent / name
            if candidate.is_file() and candidate not in found:
                found.append(candidate)
        if found:
            # 가장 가까운 곳에서 찾으면 더 올라가지 않는다. 상위 디렉터리의
            # 무관한 .env 를 주워 담는 사고를 막는다.
            break
    return tuple(found)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_env_files(),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    skiff_env: str = "local"

    #: 배포 형태.
    #:   "aws"    — S3 + DynamoDB + CloudFront + Lambda (서버리스)
    #:   "single" — EC2 한 대. nginx + 로컬 파일시스템 + SQLite
    #:
    #: 두 구현을 모두 유지하는 이유: 저장소·리포지토리를 인터페이스로 갈라두었기
    #: 때문에 라우터는 어느 쪽인지 알 필요가 없다. 도메인이 생겨 HTTPS 를 다시
    #: 쓰고 싶어지면 이 값만 바꾸면 된다.
    deploy_mode: str = "aws"

    # ── 단일 EC2 모드 ──
    #: 파일과 SQLite 가 놓이는 곳
    data_dir: str = "/var/lib/skiff"
    #: nginx 의 internal location 접두사. X-Accel-Redirect 로 넘길 때 쓴다.
    internal_files_prefix: str = "/protected"
    #: 업로드/다운로드 토큰 서명 키. 비우면 ip_hash_salt 를 재사용한다.
    url_signing_key: str = ""

    #: 관리자 페이지 토큰. **비어 있으면 관리자 라우터를 아예 등록하지 않는다.**
    #: 공개 서비스에 붙는 화면이라 "인증으로 막는다" 보다 "존재하지 않는다" 가
    #: 안전하다. 실수로 열릴 경로 자체가 없어진다.
    admin_token: str = ""

    # AWS
    aws_region: str = "ap-northeast-2"
    aws_endpoint_url: str | None = None  # LocalStack 전용. 운영에서는 None.

    # 저장소
    s3_bucket: str = "skiff-files-local"
    dynamodb_table: str = "skiff-local"

    # 서비스
    #
    # 공개 도메인은 설정에 두지 않는다. 공유 URL 은 브라우저가 location.origin 으로
    # 만들고, 서버는 코드 문자열만 돌려준다. 덕분에 CloudFront 도메인을 Lambda
    # 환경 변수로 주입하려다 생기는 순환 의존(Lambda→env→Distribution→
    # FunctionUrl→Lambda)이 아예 없다.
    ip_hash_salt: str = "change-me-in-production"

    #: CloudFront 가 오리진 요청에 붙이는 공유 비밀. 값이 있으면 이 헤더가 없는
    #: 요청을 거부한다. Lambda 함수 URL 은 공개 주소라 이게 없으면 누구나
    #: CloudFront 를 건너뛰고 직접 호출할 수 있고, 그러면 X-Forwarded-For 를
    #: 마음대로 꾸며 레이트리밋과 쿼터를 통째로 우회할 수 있다.
    #: 로컬에서는 비워 두어 검사를 끈다.
    origin_secret: str = ""

    # 다운로드 서명
    download_signer: str = "local"  # "local" | "cloudfront"
    cloudfront_domain: str = ""
    cloudfront_key_pair_id: str = ""
    cloudfront_private_key: str = ""

    @field_validator("aws_endpoint_url", mode="before")
    @classmethod
    def _blank_endpoint_is_none(cls, value: str | None) -> str | None:
        """빈 문자열을 None 으로 바꾼다.

        운영에서는 이 값을 비워 두는데(진짜 AWS 를 쓰므로), 빈 문자열을 그대로
        boto3 의 endpoint_url 로 넘기면 리전 기본 엔드포인트를 쓰지 않고
        빈 주소로 접속을 시도해 실패한다.
        """
        if value is None or not value.strip():
            return None
        return value.strip()

    @property
    def is_local(self) -> bool:
        return self.skiff_env == "local"

    @property
    def is_single_node(self) -> bool:
        return self.deploy_mode == "single"

    @property
    def signing_key(self) -> str:
        return self.url_signing_key or self.ip_hash_salt

    @property
    def files_dir(self) -> Path:
        return Path(self.data_dir) / "files"

    @property
    def sqlite_path(self) -> Path:
        return Path(self.data_dir) / "skiff.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()


# ─────────────────────────────────────────────────────────────
# 서비스 상수 — 정책 값은 전부 여기 모아 둔다
# ─────────────────────────────────────────────────────────────

KIB = 1024
MIB = 1024 * KIB
GIB = 1024 * MIB

#: 전송 1건의 파일 크기 합계 상한
MAX_TOTAL_BYTES = 1 * GIB
#: 전송 1건에 담을 수 있는 파일 개수.
#:
#: 이 값을 올릴 때 같이 봐야 하는 것: 업로드 세션을 열 때 파일마다 멀티파트를
#: 하나씩 연다. 단일 노드에서는 디렉터리를 만드는 것뿐이라 100개도 순식간이지만,
#: AWS 모드에서는 S3 API 를 파일 수만큼 **순차 호출**하므로 100개면 응답이
#: 3~5초까지 늘어난다. 그보다 더 올릴 생각이면 그 루프를 병렬화해야 한다.
MAX_FILES = 100
#: 파일명 최대 길이 (S3 키 길이 여유를 둔 값)
MAX_FILENAME_LEN = 255

#: S3 멀티파트 업로드 파트 크기. S3 최소 파트 크기는 5MiB 이므로 그보다 크게 잡는다.
PART_SIZE = 8 * MIB

#: 업로드 세션이 완료되지 않은 채 버려졌다고 보는 시간
PENDING_TTL_SECONDS = 24 * 3600

#: 업로더가 고를 수 있는 보관 기간 (초). UI 의 선택지와 1:1 대응한다.
EXPIRY_CHOICES: tuple[int, ...] = (
    3600,  # 1시간
    21600,  # 6시간
    86400,  # 24시간 (기본)
    259200,  # 3일
    604800,  # 7일
)
DEFAULT_EXPIRY = 86400

#: IP 당 하루 업로드 허용량
DAILY_QUOTA_BYTES = 5 * GIB

#: 디스크에 항상 남겨둘 여유 공간.
#:
#: 파일 서비스가 죽는 가장 흔한 방식이 디스크 포화다. 다 차면 업로드가
#: 500 으로 실패하고, 사용자는 이유를 모르며, SQLite 쓰기까지 막혀 이미
#: 올라간 파일의 메타데이터도 갱신되지 않는다. 그래서 **바닥까지 쓰지 않고**
#: 이만큼은 비워 둔 채 새 업로드를 거절한다.
#:
#: 전송 하나가 최대 1GB 이므로 그보다 넉넉히 잡는다. 거절은 명확한 안내와
#: 함께 503 으로 돌려줘, 사용자가 "왜 안 되지" 를 겪지 않게 한다.
DISK_HEADROOM_BYTES = 2 * GIB

#: 다운로드 서명 URL 유효 시간
DOWNLOAD_URL_TTL = 300

#: 애플리케이션 레벨 레이트리밋 (WAF 가 붙기 전에도 동작하는 1차 방어선)
RATE_LIMITS: dict[str, tuple[int, int]] = {
    # scope: (허용 횟수, 윈도우 초)
    "create": (10, 60),  # 업로드 세션 생성
    "lookup": (100, 300),  # 공유 코드 조회 — 열거 공격 방어의 핵심
    "download": (60, 300),
}

#: CloudFront 가 오리진 시크릿을 실어 보내는 헤더 이름.
ORIGIN_SECRET_HEADER = "x-skiff-origin"

#: 실행 파일류. 업로드는 허용하되 수신 화면에서 경고를 띄운다.
RISKY_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".exe", ".scr", ".bat", ".cmd", ".com", ".cpl", ".msi", ".ps1",
        ".vbs", ".js", ".jar", ".apk", ".app", ".dmg", ".sh", ".dll",
    }
)
