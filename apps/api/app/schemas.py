"""요청/응답 스키마. 여기서 정의한 모델이 OpenAPI 를 거쳐 프론트 타입으로 생성된다."""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

from app.config import EXPIRY_CHOICES, MAX_FILENAME_LEN, MAX_FILES, MAX_TOTAL_BYTES

Filename = Annotated[str, StringConstraints(min_length=1, max_length=MAX_FILENAME_LEN)]


# ── 업로드 세션 생성 ──────────────────────────────────────────


class FileSpec(BaseModel):
    """업로더가 올리려는 파일 1건의 명세."""

    name: Filename
    size: int = Field(ge=0, le=MAX_TOTAL_BYTES)
    mime: str = Field(default="application/octet-stream", max_length=255)


class CreateTransferRequest(BaseModel):
    files: list[FileSpec] = Field(min_length=1, max_length=MAX_FILES)
    expires_in: int = Field(default=86400, description="보관 기간(초)")

    @field_validator("expires_in")
    @classmethod
    def _known_expiry(cls, v: int) -> int:
        if v not in EXPIRY_CHOICES:
            allowed = ", ".join(str(c) for c in EXPIRY_CHOICES)
            raise ValueError(f"expires_in 은 다음 중 하나여야 합니다: {allowed}")
        return v

    @model_validator(mode="after")
    def _total_within_limit(self) -> Self:
        total = sum(f.size for f in self.files)
        if total > MAX_TOTAL_BYTES:
            raise ValueError("전송 합계가 1GB를 넘습니다")
        return self

    @property
    def total_size(self) -> int:
        return sum(f.size for f in self.files)


class FileUpload(BaseModel):
    """파일 1건을 S3 에 직접 올리기 위해 필요한 것 전부."""

    file_index: int
    key: str
    upload_id: str
    part_size: int
    part_urls: list[str]


class CreateTransferResponse(BaseModel):
    transfer_id: str
    # 공유 URL 은 서버가 만들지 않는다. 브라우저가 location.origin 에 이 코드를
    # 붙여 만든다. 서버가 자기 공개 도메인을 알 필요가 없어지므로 CloudFront
    # 도메인을 Lambda 환경 변수로 주입하는 순환 의존이 사라진다.
    code: str = Field(description="공유 코드. 예: oslo/113245")
    owner_token: str = Field(description="업로드 확정·삭제에 필요. 업로더만 보관한다.")
    expires_at: int
    uploads: list[FileUpload]


# ── 업로드 확정 ──────────────────────────────────────────────


class CompletedPart(BaseModel):
    part_number: int = Field(ge=1, le=10_000)
    etag: str


class CompletedFile(BaseModel):
    file_index: int = Field(ge=0)
    parts: list[CompletedPart] = Field(min_length=1)


class CompleteTransferRequest(BaseModel):
    owner_token: str
    files: list[CompletedFile] = Field(min_length=1, max_length=MAX_FILES)


class CompleteTransferResponse(BaseModel):
    code: str
    expires_at: int
    total_size: int


# ── 수신자용 조회 ────────────────────────────────────────────


class PublicFile(BaseModel):
    index: int
    name: str
    size: int
    mime: str
    risky: bool = Field(default=False, description="실행 파일류라 수신 화면에서 경고할 대상")


class TransferInfoResponse(BaseModel):
    code: str
    files: list[PublicFile]
    total_size: int
    created_at: int
    expires_at: int


class DownloadResponse(BaseModel):
    url: str
    expires_in: int


# ── 공통 ────────────────────────────────────────────────────


class DeleteResponse(BaseModel):
    status: Literal["deleted"] = "deleted"


class ErrorResponse(BaseModel):
    detail: str
