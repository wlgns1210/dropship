# Dropship — 프로젝트 계획서

익명 파일 전송 서비스. 삼성 Dropship(`g2sh.me`)과 유사한 형태.
업로더가 파일을 올리면 짧은 공유 링크 + QR 코드가 생기고, 수신자는 계정 없이 받는다.
링크는 업로더가 고른 기간이 지나면 만료되고 파일은 삭제된다.

---

## 0. 확정된 전제

| 항목 | 결정 |
|---|---|
| 프론트엔드 | Next.js 15 (App Router) |
| 백엔드 | FastAPI (Python 3.12) |
| 인프라 | **AWS 전용** |
| 인증 | 완전 익명 업로드 (로그인 없음) |
| MVP 기능 | QR 코드 공유 / 기간 선택형 만료 링크 |
| 파일 크기 상한 | **전송당 합계 1GB** (파일 100개까지) |
| 언어 | **한국어** (문자열을 `lib/messages.ts` 한 곳에 모아 영어 추가 여지만 남김) |

> 툴체인 메모: 이 머신에 `pnpm`·`uv`가 없어 **npm workspaces + venv/pip**로 간다. 설치할 것을 늘리지 않는 쪽을 택했다.

**만료의 정의**: 업로더가 고른 기간이 지나면 링크가 죽는다. 그게 전부다.
다운로드 횟수 제한("1회 받으면 폐기")은 **하지 않는다** — 같은 링크를 기간 내에는 몇 번이든 받을 수 있다.

MVP에서 **제외**(Phase 5로 이월): 비밀번호 보호, 다운로드 횟수 제한, 이미지·영상 미리보기, ZIP 일괄 다운로드, 다국어.
단, 데이터 모델과 API는 이 기능들이 나중에 들어올 자리를 미리 비워 둔다.

---

## 1. 핵심 설계 원칙

### 1-1. 파일 바이트는 절대 백엔드를 경유하지 않는다

이게 이 프로젝트에서 가장 중요한 결정이다.

```
[브라우저] --(1) 메타데이터만--> [FastAPI] --> [DynamoDB]
    |                                 |
    |                            (2) presigned URL 발급
    |                                 |
    +--(3) 파일 바이트 직접 업로드-----> [S3]
```

- 업로드: S3 **presigned multipart upload URL**을 FastAPI가 발급, 브라우저가 S3에 직접 PUT
- 다운로드: **CloudFront signed URL**(TTL 5분)을 발급하고 302 리다이렉트

이렇게 하면:

- FastAPI를 **Lambda에 올려도 된다**. API Gateway의 10MB 페이로드 제한이 무의미해짐 (파일이 안 지나가니까)
- 백엔드가 트래픽에 대해 사실상 상수 부하 — 5GB 파일이든 5KB 파일이든 API 호출 비용이 같다
- 서버 비용의 대부분이 S3 스토리지 + CloudFront 전송비로 수렴 → 예측 가능

### 1-2. 만료는 2중 장치로 강제한다

| 계층 | 수단 | 역할 |
|---|---|---|
| 논리적 만료 | DynamoDB `expires_at` 검사 | 즉시·정확. 만료 후 API가 410 반환 |
| 물리적 삭제 | 5분 주기 Sweeper Lambda | 실제 S3 객체 삭제 |
| 안전망 1 | DynamoDB TTL | Sweeper 실패 시 레코드 자동 정리 |
| 안전망 2 | S3 Lifecycle (30일) | 어떤 경우에도 30일 뒤 무조건 삭제 |

DynamoDB TTL은 삭제까지 최대 48시간이 걸릴 수 있어 **단독으로는 만료 수단이 될 수 없다**. 그래서 Sweeper가 주 경로, TTL은 백스톱.

### 1-3. 프론트는 SSR 없이 정적 배포

Next.js를 `output: 'export'`로 정적 빌드해서 S3 + CloudFront에 올린다.

이유:

- **AWS에서 Next.js SSR을 굴리는 건 비용 대비 손해다.** Amplify Hosting이나 OpenNext+Lambda는 설정이 늘고 콜드스타트가 붙는데, 이 서비스는 SEO가 필요 없다
- **오히려 프라이버시상 유리하다.** 공유 링크가 카카오톡·Slack에 붙었을 때 OG 프리뷰로 파일명·용량이 유출되는 사고를 원천 차단한다
- CloudFront 하나로 `/api/*` → API Gateway, `/*` → S3 라우팅 → **동일 출처, CORS 이슈 없음, 도메인 하나**

SSR이 나중에 필요해지면 Amplify Hosting으로 갈아타면 된다 (프론트 코드 수정 최소).

---

## 2. 아키텍처

```
                         ┌──────────────────┐
          사용자 ───────> │   CloudFront     │ <─── ACM 인증서 (us-east-1)
                         └────────┬─────────┘
                    /api/*        │        /*
              ┌────────────────┐  │  ┌──────────────────┐
              │ API Gateway    │<─┴─>│ S3 (정적 프론트)  │
              │  (HTTP API)    │     └──────────────────┘
              └───────┬────────┘
                      │
              ┌───────▼────────┐      ┌──────────────────┐
              │ Lambda         │─────>│ DynamoDB         │
              │ FastAPI+Mangum │      │ (전송 메타데이터) │
              └───────┬────────┘      └──────────────────┘
                      │ presigned URL 발급
                      ▼
              ┌────────────────┐
              │ S3 (파일 저장)  │<──── 브라우저 직접 업로드/다운로드
              └───────┬────────┘
                      │
    EventBridge(5분) ─> Sweeper Lambda ─> 만료 객체 삭제

              AWS WAF ─> CloudFront 앞단 레이트리밋 / 봇 차단
```

### AWS 리소스 목록

| 리소스 | 용도 | 비고 |
|---|---|---|
| S3 `dropship-files` | 파일 저장 | 버킷 비공개, CloudFront OAC로만 접근 |
| S3 `dropship-web` | 정적 프론트 | 〃 |
| CloudFront | CDN + 라우팅 + 서명 URL | 단일 배포판, 2개 오리진 |
| API Gateway (HTTP API) | FastAPI 진입점 | REST API보다 약 70% 저렴 |
| Lambda `api` | FastAPI (Mangum) | ARM64, 512MB, 30s |
| Lambda `sweeper` | 만료 정리 | EventBridge 5분 주기 |
| DynamoDB `dropship` | 메타데이터 | On-demand, 단일 테이블 |
| AWS WAF | 레이트리밋·봇 차단 | 남용 방지의 1차 방어선 |
| CloudWatch | 로그·메트릭·알람 | |
| Secrets Manager | CloudFront 서명 키페어 | |

### DB는 왜 DynamoDB인가

이 서비스의 쿼리 패턴은 딱 두 가지다.

1. 공유 코드로 단건 조회 (`GET /{word}/{number}`)
2. 만료된 전송 목록 조회 (Sweeper)

조인도, 트랜잭션도, 복잡한 집계도 없다. 이건 DynamoDB가 제일 잘하는 모양이고, on-demand로 쓰면 **MVP 트래픽에서 월 몇 천 원**이다. RDS는 안 쓰는 시간에도 인스턴스 비용이 나간다 (t4g.micro 약 $12/mo, Aurora Serverless v2는 더 비쌈).

> **다만**: 팀이 SQLAlchemy에 훨씬 익숙하거나 나중에 관리자 대시보드에서 자유로운 통계 쿼리가 필요하다면 RDS Postgres로 바꿔도 된다. 레포지토리 계층을 인터페이스로 분리해 두어 교체 비용을 낮춘다.

### 데이터 모델 (DynamoDB 단일 테이블)

```python
# Transfer 레코드
{
  "PK": "T#oslo-113245",          # 공유 코드
  "SK": "META",
  "transfer_id": "01J8X...",       # ULID
  "status": "pending|ready|expired|deleted",
  "files": [
    {"key": "01J8X.../0/report.pdf", "name": "report.pdf",
     "size": 1048576, "mime": "application/pdf", "etag": "..."}
  ],
  "total_size": 1048576,
  "created_at": 1757800000,
  "expires_at": 1757886400,        # ← 사용자가 고른 기간. 유일한 만료 기준
  "download_count": 0,             # 통계용. 접근 제한에는 쓰지 않음
  "creator_ip_hash": "sha256(ip+salt)",  # 원본 IP 저장 안 함
  "password_hash": None,           # Phase 5 대비 자리
  "max_downloads": None,           # Phase 5 대비 자리. MVP에서는 항상 None
  "ttl": 1757890000,               # DynamoDB TTL (expires_at + 1h)

  "GSI1PK": "EXPIRY",              # Sweeper용
  "GSI1SK": 1757886400
}

# IP 일일 쿼터 카운터
{
  "PK": "Q#<ip_hash>#2026-09-14",
  "SK": "QUOTA",
  "bytes_used": 3221225472,
  "ttl": 1757980000
}
```

**GSI1** (`GSI1PK`=EXPIRY, `GSI1SK`=expires_at): Sweeper가 `GSI1SK < now` 조건으로 만료 건만 정확히 긁어온다. 풀스캔 없음.

---

## 3. 공유 링크 설계

### 형식

삼성 Dropship과 동일한 `단어/숫자` 패턴을 쓴다: `dropship.app/oslo/113245`

```
/{word}/{number}
  word   = 큐레이션된 2,048개 단어 목록 (도시명·자연어, 발음 쉬운 것)
  number = 100000–999999 (6자리)
  경우의 수 = 2,048 × 900,000 ≈ 18억
```

전화로 불러주거나 손으로 입력하기 쉽다는 게 이 형식의 장점이다.

### ⚠️ 열거(enumeration) 공격 리스크 — 반드시 짚고 갈 부분

18억 조합은 **암호학적으로 안전한 수준이 아니다**. 단어 목록이 공개되면 공격자가 무작위로 때려서 남의 파일을 주울 수 있다. 익명 업로드라 더더욱 위험하다.

대응 3단계:

1. **AWS WAF 레이트리밋** — 조회 엔드포인트에 IP당 5분/100요청 제한. 열거 속도를 실용 불가 수준으로 떨어뜨림
2. **다운로드 토큰 분리** — 예쁜 코드는 *메타데이터 페이지*까지만 열어준다. 실제 파일은 그 다음 단계에서 발급되는 5분짜리 CloudFront 서명 URL로만 받을 수 있다. 코드를 맞춰도 크롤러가 자동으로 파일을 빨아가진 못함
3. **404 구분 불가** — 존재하지 않는 코드와 만료된 코드에 동일한 응답 + 동일한 지연시간을 준다 (타이밍 사이드채널 차단)

Phase 5의 비밀번호 보호가 들어오면 이 리스크는 사실상 해소된다. **MVP 기간에는 "민감한 파일은 올리지 마세요" 고지를 UI에 명시할 것을 권장한다.**

### 만료 기간 선택지

UI에서 업로더가 고른다. 기본값 **24시간**.

| 옵션 | 값(초) |
|---|---|
| 1시간 | `3600` |
| 6시간 | `21600` |
| **24시간 (기본)** | `86400` |
| 3일 | `259200` |
| 7일 | `604800` |

---

## 4. API 설계

### `POST /api/transfers` — 업로드 세션 생성

```jsonc
// 요청
{
  "files": [{"name": "report.pdf", "size": 1048576, "mime": "application/pdf"}],
  "expires_in": 86400
}

// 응답 201
{
  "transfer_id": "01J8X...",
  "code": "oslo/113245",
  "share_url": "https://dropship.app/oslo/113245",
  "delete_token": "unguessable-...",
  "expires_at": 1757886400,
  "uploads": [{
    "file_index": 0,
    "upload_id": "2~abc...",
    "key": "01J8X.../0/report.pdf",
    "part_size": 8388608,
    "part_urls": ["https://s3...", "..."]
  }]
}
```

검증: 파일 개수 ≤ 100, **합계 ≤ 1GB**, IP 일일 쿼터 잔여량, 파일명 sanitize(한글 파일명 보존).

### `POST /api/transfers/{id}/complete` — 업로드 확정

파트 ETag 목록을 받아 S3 `CompleteMultipartUpload` 실행 → `HeadObject`로 실존·크기 검증 → `status: ready`.
**이 단계 전까지 공유 링크는 동작하지 않는다** (반쪽 업로드 노출 방지).

### `GET /api/transfers/{code}` — 수신자용 메타데이터

```jsonc
// 200
{"files": [{"name": "report.pdf", "size": 1048576, "mime": "application/pdf"}],
 "total_size": 1048576, "expires_at": 1757886400}
// 410 Gone — 만료/미존재 (구분 불가하게 동일 응답)
```

`expires_at`을 내려주므로 수신 페이지에서 "23시간 뒤 만료" 카운트다운을 보여줄 수 있다.

### `GET /api/transfers/{code}/download/{index}` — 다운로드

`expires_at` 검사 → CloudFront 서명 URL(TTL 5분) 302 리다이렉트 → `download_count` 증가(통계용, 실패해도 다운로드를 막지 않는 fire-and-forget).

`Content-Disposition: attachment; filename*=UTF-8''...` 를 서명 URL에 심어 원본 파일명·한글 파일명을 보존한다.

### `DELETE /api/transfers/{id}` — 업로더 즉시 삭제

업로드 응답에만 담기는 `delete_token`으로 인증. 익명이라 계정 기반 소유권 확인이 불가능하므로 토큰 방식.

### `GET /api/transfers/{code}/qr` — QR 이미지

`qrcode[pil]`로 PNG 생성. 프론트에서 클라이언트 사이드로 그려도 되지만(`qrcode.react`), 서버 생성분은 카카오톡 공유·이미지 저장용으로 유용하다.

---

## 5. 남용 방지 (익명 서비스의 최대 리스크)

로그인이 없다는 건 **아무나 무제한으로 불법 콘텐츠 유통 채널을 만들 수 있다**는 뜻이다. 여기를 대충 하면 서비스가 죽는다.

| 장치 | 구현 |
|---|---|
| IP 일일 쿼터 | 5GB/일. DynamoDB 카운터, IP는 해시로만 저장 |
| 파일 크기 제한 | **전송당 합계 1GB**, 파일 100개 |
| WAF 레이트리밋 | 업로드 생성 IP당 10/분, 조회 IP당 100/5분 |
| WAF Bot Control | 자동화 트래픽 차단 |
| CAPTCHA | 쿼터 임계치 초과 시 AWS WAF CAPTCHA 발동 |
| 확장자 정책 | `.exe .scr .bat .cmd .ps1 .jar .apk` 등 경고 또는 차단 |
| 신고 창구 | 수신 페이지에 "신고" 버튼 + `abuse@` 메일. 신고 시 즉시 비활성 가능한 어드민 경로 |
| 감사 로그 | 업로드/다운로드 IP 해시·시각을 CloudWatch에 90일 보존 |

### 법적·운영 준비물 (개발과 병행)

- 이용약관 / 개인정보처리방침 (한국 서비스라면 정보통신망법 대응)
- 저작권 침해 신고·삭제 절차 (DMCA 유사 프로세스)
- 불법촬영물 등 유통 방지 의무 — 서비스 규모에 따른 적용 여부 확인 필요
- 로그 보존 기간 정책 명시

---

## 6. 레포 구조

```
Dropship/
├─ apps/
│  ├─ web/                       # Next.js 15 (static export)
│  │  ├─ app/
│  │  │  ├─ page.tsx             # 업로드 화면
│  │  │  ├─ s/[code]/page.tsx    # 수신 화면 (CSR)
│  │  │  └─ layout.tsx
│  │  ├─ components/
│  │  │  ├─ Dropzone.tsx         # 드래그앤드롭 + 진행률
│  │  │  ├─ ExpirySelect.tsx     # 기간 선택
│  │  │  ├─ ShareResult.tsx      # 링크 + QR + 복사
│  │  │  └─ FileList.tsx
│  │  ├─ lib/
│  │  │  ├─ uploader.ts          # S3 멀티파트 클라이언트 (핵심)
│  │  │  └─ api.ts               # 자동 생성 타입 클라이언트
│  │  └─ next.config.ts
│  │
│  └─ api/                       # FastAPI
│     ├─ app/
│     │  ├─ main.py              # FastAPI 앱 + Mangum 핸들러
│     │  ├─ routers/transfers.py
│     │  ├─ schemas.py           # Pydantic v2
│     │  ├─ services/
│     │  │  ├─ storage.py        # S3 추상화 (presign/multipart/delete)
│     │  │  ├─ repository.py     # DynamoDB 추상화
│     │  │  ├─ codes.py          # word/number 생성 + 충돌 재시도
│     │  │  ├─ quota.py          # IP 쿼터
│     │  │  └─ signing.py        # CloudFront 서명 URL
│     │  ├─ wordlist.py          # 2,048 단어
│     │  └─ sweeper.py           # 만료 정리 Lambda 핸들러
│     ├─ tests/
│     └─ pyproject.toml
│
├─ infra/                        # AWS CDK (Python)
│  ├─ app.py
│  └─ stacks/{storage,api,web,monitoring}.py
│
├─ docker-compose.yml            # LocalStack (S3) + DynamoDB Local
├─ Makefile
└─ docs/
   ├─ PLAN.md                    # 이 문서
   └─ API.md
```

### 두 언어를 쓰는 비용을 어떻게 줄이나

FastAPI가 OpenAPI 스키마를 자동 생성한다. 이걸 `openapi-typescript`로 돌려 **TS 타입을 자동 생성**하고 `apps/web/lib/api.ts`에 넣는다. Pydantic 모델을 고치면 프론트 타입이 따라 바뀌고, 안 맞으면 `tsc`가 잡는다. CI에 "생성된 타입이 최신인지" 체크를 넣어 드리프트를 막는다.

인프라를 **CDK Python**으로 쓰는 것도 같은 이유다 — 백엔드와 언어를 통일해서 관리 대상 언어를 2개로 묶는다.

---

## 7. 구현 단계

### Phase 0 — 스캐폴딩 (0.5일)

- 모노레포 초기화, Git, venv+pip(Python) / npm workspaces(Node)
- `docker-compose`: LocalStack(S3) + DynamoDB Local → **AWS 계정 없이 전 기능 로컬 개발 가능하게**
- Makefile: `make dev` 한 방에 웹+API+로컬 AWS 기동
- lint/format: ruff + mypy, eslint + prettier

### Phase 1 — 업로드/다운로드 코어 (2–3일) ★ 가장 어려운 구간

- `storage.py`: presigned multipart 발급 / complete / abort / delete
- `repository.py`: DynamoDB CRUD + 조건부 업데이트
- `POST /transfers`, `POST /transfers/{id}/complete`
- **`uploader.ts`**: 브라우저 청크 분할, 병렬 업로드(동시 4개), 진행률, 실패 파트 재시도, 중단/재개
- 5GB 실파일 업로드 테스트

> 여기가 프로젝트 난이도의 80%다. 멀티파트 업로드는 파트 재시도·취소·메모리 관리에서 함정이 많다. 여유 있게 잡을 것.

### Phase 2 — 공유 링크 + QR + 만료 (1–2일)

- `codes.py`: 단어+숫자 생성, 충돌 시 재시도, 혼동 문자 회피
- 기간 선택 UI, `expires_at` 저장·검증
- 수신 페이지 `/s/[code]` + CloudFront 라우팅 + 만료 카운트다운
- QR 생성(서버 PNG + 클라이언트 SVG)
- 다운로드 엔드포인트 + 서명 URL

### Phase 3 — 남용 방지 + 정리 (1–2일)

- Sweeper Lambda + EventBridge 5분 스케줄
- IP 쿼터, 파일 크기·개수 검증, 확장자 정책
- 만료/미존재 응답 통일, 타이밍 정규화 (열거 공격 대응)
- `delete_token` 기반 즉시 삭제

### Phase 4 — AWS 배포 (2–3일)

- CDK 스택 작성 → `cdk deploy`
- 도메인 + ACM 인증서(us-east-1 필수) + Route53
- CloudFront 2-오리진 라우팅, OAC, 서명 키페어
- WAF 룰 부착
- GitHub Actions CI/CD (테스트 → 빌드 → 배포 → CloudFront 무효화)
- CloudWatch 대시보드 + 알람 (5xx, Lambda 에러, 스토리지 증가율, 비용)

### Phase 5 — 다듬기 (지속)

- 비밀번호 보호 ← **보안상 가장 우선순위 높은 후속 기능**
- 다운로드 횟수 제한 / 1회 수신 후 폐기 (`max_downloads` 자리는 이미 비워 둠)
- 이미지·영상 썸네일 미리보기
- ZIP 일괄 다운로드
- i18n (ko/en), 다크모드, PWA
- 붙여넣기(Ctrl+V) 업로드, 텍스트 스니펫 공유

**총 MVP 예상: 7–11일** (1인 기준, Phase 0–4)

---

## 8. 예상 비용 (월, 서울 리전 기준 개략)

MVP 트래픽(월 1,000건 전송 / 평균 200MB / 건당 2회 다운로드 ≈ 400GB 전송) 가정:

| 항목 | 비용 |
|---|---|
| CloudFront 전송 400GB | ~$45 |
| S3 스토리지 (평균 30GB 상주) | ~$1 |
| S3 요청 | ~$1 |
| Lambda + API Gateway | ~$1 |
| DynamoDB on-demand | ~$1 |
| WAF | ~$8 |
| Route53 | ~$1 |
| **합계** | **~$58/월** |

**비용의 대부분이 CloudFront 전송비**다. 트래픽이 커지면 여기부터 손대야 한다.

> **참고**: Cloudflare R2는 egress가 무료라 이 워크로드에서 압도적으로 저렴하지만, "AWS 전용" 제약에 따라 제외했다. 나중에 전송비가 부담되면 **S3는 유지하고 CDN만 교체하는** 하이브리드가 현실적인 탈출구다.

---

## 9. 남은 확인 사항

1. **도메인** — `g2sh.me` 같은 짧은 도메인이 필요하다. 확보한 도메인이 있나? (Phase 4에서 필요)
2. **AWS 계정 상태** — 기존 계정에 올리나, 신규 계정인가? 리전은 서울(ap-northeast-2)로 가정했다.
3. **서비스 성격** — 개인 프로젝트/포트폴리오인가, 실제 공개 운영인가? 후자라면 5절의 법적 준비물과 신고 대응 체계를 Phase 4에 포함시켜야 한다.

### 해결된 항목

- ~~"1회용 링크"의 의미~~ → **기간 만료만 의미함.** 다운로드 횟수 제한은 MVP 범위 밖(Phase 5).
- ~~파일 크기 상한~~ → **전송당 합계 1GB.**
- ~~언어~~ → **한국어.**
