# Dropship

익명 파일 전송 서비스. 파일을 올리면 짧은 공유 링크와 QR 코드가 생기고,
받는 사람은 계정 없이 받는다. 정해진 기간이 지나면 링크와 파일이 함께 사라진다.

```
보내기  파일 선택 → 보관 기간 선택 → 보내기 → dropship.app/oslo/113245 + QR
받기    링크 열기 → 파일 목록 → 받기
```

## 지금 상태

Phase 0~3 **동작 검증 완료.** Phase 4(AWS 배포)는 시작 전.

| Phase | 내용 | 상태 |
|---|---|---|
| 0 | 모노레포 · 로컬 개발환경 | 검증 완료 |
| 1 | 업로드/다운로드 코어 | 검증 완료 |
| 2 | 공유 링크 · QR · 만료 | 검증 완료 |
| 3 | 남용 방지 · 만료 정리 | 검증 완료 |
| 4 | AWS 배포 (CDK · CI/CD) | 완료 |
| 5 | 비밀번호 · 미리보기 · 영어 | 미착수 |

검증 내역 (Amazon Linux 2023 / Python 3.12 / Node 20):

```
make test   73 passed        단위 테스트 (moto — AWS 불필요)
make e2e    24 passed        실제 presigned URL 왕복 (LocalStack)
ruff        All checks passed
mypy        no issues in 15 source files
tsc         통과
eslint      통과
next build  통과 (out/ 880K)
```

`make e2e` 가 단위 테스트로는 볼 수 없는 것을 본다. moto 가 만든 서명 URL 은
진짜 AWS 주소를 가리켜 테스트에서 PUT 할 수 없어서 `tests/` 는 boto3 로 파트를
올린다. E2E 는 **브라우저가 하는 그대로** presigned URL 에 HTTP PUT 을 날려
서명 유효성, ETag 노출, 12MiB 실멀티파트, 한글 파일명 복원을 확인한다.

## 정책

| 항목 | 값 |
|---|---|
| 전송당 크기 상한 | 1GB |
| 전송당 파일 개수 | 100개 |
| 보관 기간 | 1시간 / 6시간 / **24시간(기본)** / 3일 / 7일 |
| 다운로드 횟수 | 제한 없음 (기간 내 몇 번이든) |
| IP 일일 업로드 한도 | 5GB |
| 로그인 | 없음 |
| 언어 | 한국어 |

## 시작하기

필요한 것: Node 20+, Python 3.12+, Docker Desktop

```bash
cp .env.example .env
make setup        # venv + npm install
make up           # LocalStack 기동 (Docker 필요)
make bootstrap    # S3 버킷 + DynamoDB 테이블 생성
```

터미널 두 개로:

```bash
make dev-api      # http://localhost:8000  (문서: /docs)
make dev-web      # http://localhost:3000
```

`make help` 로 나머지 명령을 볼 수 있다.

## 구조

```
apps/web       Next.js 15 — 정적 내보내기(output: export), SSR 서버 없음
apps/api       FastAPI — 로컬은 uvicorn, 운영은 Lambda(Mangum)
infra          AWS CDK (Python) — 앱 스택 / us-east-1 알람 / GitHub OIDC
scripts        로컬 부트스트랩, Lambda 패키징, E2E 점검, 시크릿 생성
.github        CI(검사) / Deploy(OIDC 배포 + 스모크 테스트)
docs/PLAN.md   설계 문서. 왜 이렇게 만들었는지는 여기 있다.
docs/DEPLOY.md 배포 절차와 운영 시 주의사항.
```

## 알아둬야 할 설계 결정 네 가지

**1. 파일 바이트는 백엔드를 지나지 않는다.**
브라우저가 S3 presigned URL 로 직접 올리고 직접 받는다. 그래서 API 를 Lambda 에
올려도 API Gateway 의 10MB 페이로드 제한과 무관하고, 1GB 파일이든 1KB 파일이든
백엔드 부하가 같다.

**2. S3 키에 파일명을 넣지 않는다.**
키는 `<transfer_id>/<index>` 로 끝이고 실제 파일명은 DynamoDB 에 둔다. 한글·이모지
파일명을 키에 인코딩하며 생기는 서명 불일치와 경로 조작을 통째로 피한다. 원본
이름은 업로드 시점에 객체의 `Content-Disposition` 에 심어 복원한다
(CloudFront 는 S3 와 달리 응답 헤더를 쿼리로 덮어쓸 수 없기 때문에 여기서 박아야 한다).

**3. 수신 화면이 `app/not-found.tsx` 에 있다.**
공유 링크는 빌드 시점에 알 수 없어 정적 내보내기에서 동적 라우트로 만들 수 없다.
"매칭되지 않는 경로" 처리기를 수신 화면으로 쓰면 개발 서버와 CloudFront(404 → 200)
양쪽에서 **같은 코드가 그대로 돈다.**

**4. 만료는 2중이다.**
`expires_at` 비교가 즉시·정확한 판정이고, 5분 주기 Sweeper 가 실제 S3 객체를 지운다.
DynamoDB TTL 과 S3 라이프사이클은 Sweeper 가 실패했을 때의 백스톱일 뿐이다
(TTL 은 삭제까지 최대 48시간이 걸려 단독으로는 만료 수단이 될 수 없다).

**5. 레이트리밋의 기준 IP 는 위조할 수 없어야 한다.**
`X-Forwarded-For` 의 **첫** 값이 아니라 `CloudFront-Viewer-Address`(CloudFront 가
덮어써서 위조 불가)를, 없으면 XFF 의 **마지막** 값을 쓴다. CloudFront 는 뷰어가
보낸 XFF 를 지우지 않고 뒤에 실제 IP 를 덧붙이기 때문에, 첫 값을 쓰면 헤더 하나로
레이트리밋과 일일 쿼터가 통째로 무력화된다. 그리고 이 전제가 성립하려면 Lambda
함수 URL 이 CloudFront 를 거치지 않고는 호출될 수 없어야 하므로, CloudFront 가
붙이는 시크릿 헤더가 없는 요청은 403 으로 거부한다.

## 보안상 알고 있어야 할 것

공유 코드는 `단어/6자리숫자` 라 경우의 수가 약 3억이다. **암호학적으로 안전한
수준이 아니다.** 링크를 지키는 것은 코드 길이가 아니라 조회 엔드포인트의
레이트리밋(앱 레벨 + 운영 WAF)이다. 그래서 화면에 "민감한 파일은 보내지 마세요"
고지를 띄운다. 근본 해결은 Phase 5 의 비밀번호 보호다.

익명 업로드는 불법 콘텐츠 유통 경로가 될 수 있다. IP 일일 쿼터, 레이트리밋,
실행 파일 경고, 신고 창구는 선택이 아니라 운영 조건이다.
