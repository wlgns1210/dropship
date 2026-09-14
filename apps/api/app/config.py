"""환경 설정과 서비스 전역 상수."""

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 레포 루트의 .env 를 읽는다 (apps/api/app/config.py → 3단계 위)
_REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(_REPO_ROOT / ".env", _REPO_ROOT / ".env.example"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    dropship_env: str = "local"

    # AWS
    aws_region: str = "ap-northeast-2"
    aws_endpoint_url: str | None = None  # LocalStack 전용. 운영에서는 None.

    # 저장소
    s3_bucket: str = "dropship-files-local"
    dynamodb_table: str = "dropship-local"

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
        return self.dropship_env == "local"


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
#: 전송 1건에 담을 수 있는 파일 개수
MAX_FILES = 20
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
ORIGIN_SECRET_HEADER = "x-dropship-origin"

#: 실행 파일류. 업로드는 허용하되 수신 화면에서 경고를 띄운다.
RISKY_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".exe", ".scr", ".bat", ".cmd", ".com", ".cpl", ".msi", ".ps1",
        ".vbs", ".js", ".jar", ".apk", ".app", ".dmg", ".sh", ".dll",
    }
)
