/**
 * FastAPI 클라이언트.
 *
 * 아래 타입들은 손으로 적었지만, `npm run gen:api` 가 FastAPI 의 OpenAPI 스키마에서
 * 같은 타입을 뽑아 `lib/api-types.ts` 에 넣고 CI 가 둘의 일치를 검사한다.
 * 백엔드 Pydantic 모델을 고치고 프론트를 안 고치면 타입 검사에서 걸린다.
 */

// 운영에서는 CloudFront 가 프론트와 API 를 같은 도메인에 묶으므로 빈 값이면 된다.
const BASE = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "").replace(/\/$/, "");

export interface FileSpec {
  name: string;
  size: number;
  mime: string;
}

export interface FileUpload {
  file_index: number;
  key: string;
  upload_id: string;
  part_size: number;
  part_urls: string[];
}

export interface CreateTransferResponse {
  transfer_id: string;
  /** 공유 URL 은 서버가 주지 않는다. shareUrlFor(code) 로 브라우저가 만든다. */
  code: string;
  owner_token: string;
  expires_at: number;
  uploads: FileUpload[];
}

export interface CompletedPart {
  part_number: number;
  etag: string;
}

export interface CompleteTransferResponse {
  code: string;
  expires_at: number;
  total_size: number;
}

export interface PublicFile {
  index: number;
  name: string;
  size: number;
  mime: string;
  risky: boolean;
}

export interface TransferInfo {
  code: string;
  files: PublicFile[];
  total_size: number;
  created_at: number;
  expires_at: number;
}

export interface ClientConfig {
  max_total_bytes: number;
  max_files: number;
  expiry_choices: number[];
  daily_quota_bytes: number;
}

/**
 * 공유 URL 은 브라우저가 만든다.
 *
 * 서버가 만들려면 자기 공개 도메인을 알아야 하는데, 그러려면 CloudFront 도메인을
 * Lambda 환경 변수로 넣어야 하고 그건 순환 의존이다(Lambda→env→Distribution→
 * FunctionUrl→Lambda). 공유하는 사람의 브라우저는 자기가 어느 도메인에 있는지
 * 이미 정확히 알고 있으므로 여기서 만드는 것이 맞다.
 */
export function shareUrlFor(code: string): string {
  return `${window.location.origin}/${code}`;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
  ) {
    super(detail);
    this.name = "ApiError";
  }

  /** 410 은 "없거나 만료됨". 서버가 둘을 일부러 구분해 주지 않는다. */
  get isGone(): boolean {
    return this.status === 410;
  }

  get isRateLimited(): boolean {
    return this.status === 429;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...init?.headers,
      },
    });
  } catch {
    // fetch 가 throw 하는 것은 네트워크 실패뿐이다 (HTTP 오류는 throw 하지 않는다).
    throw new ApiError(0, "network");
  }

  if (!response.ok) {
    throw new ApiError(response.status, await extractDetail(response));
  }
  return (await response.json()) as T;
}

async function extractDetail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
    // Pydantic 검증 실패는 detail 이 배열로 온다.
    if (Array.isArray(body.detail) && body.detail.length > 0) {
      const first = body.detail[0] as { msg?: string };
      if (first?.msg) return first.msg;
    }
  } catch {
    /* 본문이 JSON 이 아니면 상태 코드만으로 판단한다 */
  }
  return `HTTP ${response.status}`;
}

export const api = {
  config: () => request<ClientConfig>("/api/config"),

  createTransfer: (body: { files: FileSpec[]; expires_in: number }) =>
    request<CreateTransferResponse>("/api/transfers", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  completeTransfer: (
    code: string,
    body: { owner_token: string; files: { file_index: number; parts: CompletedPart[] }[] },
  ) =>
    request<CompleteTransferResponse>(`/api/transfers/${code}/complete`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  getTransfer: (code: string) => request<TransferInfo>(`/api/transfers/${code}`),

  downloadUrl: (code: string, index: number) =>
    request<{ url: string; expires_in: number }>(
      `/api/transfers/${code}/download/${index}`,
    ),

  /** 파일이 여러 개일 때 ZIP 하나로 받는 URL. */
  downloadAllUrl: (code: string) =>
    request<{ url: string; expires_in: number }>(`/api/transfers/${code}/download-all`),

  deleteTransfer: (code: string, ownerToken: string) =>
    request<{ status: string }>(
      `/api/transfers/${code}?owner_token=${encodeURIComponent(ownerToken)}`,
      { method: "DELETE" },
    ),
};
