# Skiff

익명 파일 전송 서비스. 파일을 올리면 짧은 공유 링크와 QR 코드가 생기고,
받는 사람은 계정 없이 받는다. 정해진 기간이 지나면 링크와 파일이 함께 사라진다.

```
보내기  파일 선택 → 보관 기간 선택 → 보내기 → <호스트>/oslo/113245 + QR
받기    링크 열기 → 파일 목록 → 받기
```

## 지금 상태

Phase 0~4 **동작 검증 완료.** 단일 VM 에 컨테이너로 배포되어 돌고 있다.

| Phase | 내용 | 상태 |
|---|---|---|
| 0 | 모노레포 · 로컬 개발환경 | 검증 완료 |
| 1 | 업로드/다운로드 코어 | 검증 완료 |
| 2 | 공유 링크 · QR · 만료 | 검증 완료 |
| 3 | 남용 방지 · 만료 정리 | 검증 완료 |
| 4 | 배포 (컨테이너 · CDK · CI) | 검증 완료 |
| 5 | 비밀번호 · 미리보기 · 영어 | 미착수 |

검증 내역 (Python 3.12 / Node 20):

```
make test    183 passed       단위 테스트 (moto — AWS 불필요)
make e2e      23 passed       실제 업로드/다운로드 왕복
ruff         All checks passed
mypy         22 source files
tsc          통과
eslint       통과
next build   통과 (out/ 932K)
```

배포본에는 본문까지 확인하는 스모크 점검을 따로 돌린다. 상태 코드만 보면
안 되기 때문이다 — 매칭되지 않는 경로가 전부 `404.html` 을 200 으로 돌려주는
설계라, `/admin` 이 404 화면을 띄우면서도 점검을 통과한 적이 있다.

```
bash deploy/verify.sh http://<주소>       11 passed
```

`make e2e` 가 단위 테스트로는 볼 수 없는 것을 본다. E2E 는 **브라우저가 하는
그대로** 서명 URL 에 HTTP PUT 을 날려 서명 유효성, ETag 노출, 12MiB 실멀티파트,
한글 파일명 복원, 열거 방어, 소유자 전용 삭제를 확인한다. `SKIFF_API` 를 주면
배포된 주소로도 같은 스크립트가 돈다 — 로컬에서만 확인하고 배포는 눈으로 때우면,
로컬과 운영의 차이에서 나는 문제를 사용자가 먼저 발견하게 된다.

## 정책

| 항목 | 값 |
|---|---|
| 전송당 크기 상한 | 1GB |
| 전송당 파일 개수 | 100개 (2개 이상이면 ZIP 일괄 받기) |
| 보관 기간 | 1시간 / 6시간 / **24시간(기본)** / 3일 / 7일 |
| 다운로드 횟수 | 제한 없음 (기간 내 몇 번이든) |
| IP 일일 업로드 한도 | 5GB |
| 디스크 최소 여유 | 2GB (미만이면 새 업로드 거절) |
| 로그인 | 없음 |
| 언어 | 한국어 |

## 컨테이너로 실행

```bash
cp docker/env.example .env      # 비밀값을 채운다 (scripts/gen_secrets.py)
docker compose up -d --build
```

`api`(FastAPI) · `web`(nginx + 정적 산출물) · `sweeper`(주기적 정리) 세 개가 뜬다.
호스트 배포와 같은 구성이고 라우팅 규칙도 같은 파일(`deploy/nginx-app.inc`)을 쓴다.

| 항목 | 값 |
|---|---|
| 이미지 크기 | api 322MB · web 75MB |
| 공개 포트 | `SKIFF_PORT`(기본 8090) → 컨테이너 80 |
| 데이터 | 이름 있는 볼륨 `skiff_data` (업로드 파일 + SQLite) |

다른 VM 에는 `compose.deploy.yaml` 로 ghcr.io 이미지를 받아 띄운다. 저장소를
받을 필요 없이 그 파일과 `.env` 두 개면 된다 — [docs/DEPLOY.md](docs/DEPLOY.md).

주의할 것 두 가지:

- **TLS 가 없다.** 컨테이너 배포는 앞단(로드밸런서·리버스 프록시)이 인증서를
  끝내는 것이 전제다. 평문으로 공개하면 파일이 그대로 오간다. 링크 복사도
  `navigator.clipboard` 가 보안 컨텍스트를 요구해 막히는데, 이쪽은
  `window.prompt` 로 떨어져 버튼이 죽지는 않는다.
- **`docker compose down -v` 를 조심할 것.** `-v` 는 볼륨까지 지워서 업로드된
  파일과 모든 공유 링크가 사라진다.

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
apps/api       FastAPI — uvicorn(컨테이너·로컬) 또는 Lambda(Mangum, AWS 모드)
docker         컨테이너 이미지 — api · web(nginx) · sweeper
deploy         nginx 라우팅 규칙과 배포 후 스모크 점검(verify.sh)
infra          AWS CDK (Python) — 앱 스택 / us-east-1 알람 / GitHub OIDC
scripts        로컬 부트스트랩, Lambda 패키징, E2E 점검, 시크릿 생성
.github        CI(검사) / Image(ghcr.io 이미지) / Deploy(OIDC 배포)
docs/PLAN.md   설계 문서. 왜 이렇게 만들었는지는 여기 있다.
docs/DEPLOY.md 배포 절차와 운영 시 주의사항.
```

## 배포 형태가 두 가지다

저장소·스토리지를 인터페이스 뒤에 두어 `DEPLOY_MODE` 한 값으로 갈린다.
라우터는 어느 쪽인지 모른 채 돈다.

| | `single` (현재 운영) | `aws` |
|---|---|---|
| 메타데이터 | SQLite (WAL) | DynamoDB |
| 파일 | 로컬 디스크 | S3 |
| 실행 | 컨테이너 uvicorn | Lambda (Mangum) |
| 앞단 | nginx | CloudFront |
| 다운로드 | `X-Accel-Redirect` | S3 presigned URL |

## 알아둬야 할 설계 결정 다섯 가지

**1. 파일 바이트는 되도록 애플리케이션을 지나지 않는다.**
AWS 모드에서는 브라우저가 S3 presigned URL 로 직접 올리고 직접 받는다. 그래서
API 를 Lambda 에 올려도 페이로드 제한과 무관하다. 단일 노드 모드에서도 **내려보낼
때는** 같다 — API 는 토큰만 확인하고 `X-Accel-Redirect` 를 돌려주면 nginx 가
`sendfile` 로 보낸다. 업로드만 API 를 지나는데, 파트 단위로 스트리밍해 디스크에
바로 쓰므로 파일 크기와 메모리 사용량은 무관하다.

**2. 저장소 키에 파일명을 넣지 않는다.**
키는 `<transfer_id>/<index>` 로 끝이고 실제 파일명은 메타데이터(SQLite·DynamoDB)에
둔다. 한글·이모지 파일명을 키에 인코딩하며 생기는 서명 불일치와 경로 조작을 통째로
피한다. 원본 이름은 `Content-Disposition` 으로 복원한다.

**3. 수신 화면이 `app/not-found.tsx` 에 있다.**
공유 링크는 빌드 시점에 알 수 없어 정적 내보내기에서 동적 라우트로 만들 수 없다.
"매칭되지 않는 경로" 처리기를 수신 화면으로 쓰면 개발 서버와 배포본(404 → 200)
양쪽에서 **같은 코드가 그대로 돈다.** 대신 이 설계 때문에 배포 점검에서 상태
코드만 보면 안 된다 — 아무 경로나 200 이 나오므로 본문을 봐야 한다.

**4. 만료는 2중이다.**
`expires_at` 비교가 즉시·정확한 판정이고, 5분 주기 Sweeper 가 실제 파일을 지운다.
DynamoDB TTL 과 S3 라이프사이클(AWS 모드)은 Sweeper 가 실패했을 때의 백스톱일
뿐이다 — TTL 은 삭제까지 최대 48시간이 걸려 단독으로는 만료 수단이 될 수 없다.

**5. 레이트리밋의 기준 IP 는 위조할 수 없어야 한다.**
`X-Forwarded-For` 의 **첫** 값이 아니라 **마지막** 값을 쓴다. 프록시는 뷰어가 보낸
XFF 를 지우지 않고 뒤에 실제 IP 를 덧붙이기 때문에, 첫 값을 쓰면 헤더 하나로
레이트리밋과 일일 쿼터가 통째로 무력화된다. 가장 가까운 신뢰 프록시가 덧붙인
마지막 값만 믿을 수 있다.

CloudFront 뒤에서는 `CloudFront-Viewer-Address`(CloudFront 가 덮어써서 위조 불가)를
먼저 본다. 다만 **그 구성일 때만** 본다 — nginx 앞에서는 같은 헤더를 클라이언트가
직접 채워 보낼 수 있어서, 무조건 믿으면 방어가 그대로 뚫린다. 그래서 배포 형태를
인자로 받아 믿을 수 있는 구성에서만 읽는다. 그리고 이 전제가 성립하려면 Lambda
함수 URL 이 CloudFront 를 거치지 않고는 호출될 수 없어야 하므로, CloudFront 가
붙이는 시크릿 헤더가 없는 요청은 403 으로 거부한다.

## 보안상 알고 있어야 할 것

공유 코드는 `단어/6자리숫자` 라 경우의 수가 약 3억이다. **암호학적으로 안전한
수준이 아니다.** 링크를 지키는 것은 코드 길이가 아니라 조회 엔드포인트의
레이트리밋(조회 100회/5분)이다. 그래서 화면에 "민감한 파일은 보내지 마세요"
고지를 띄운다. 근본 해결은 Phase 5 의 비밀번호 보호다. WAF 는 켜져 있지 않다 —
코드 공간이 3억이라 이 레이트리밋만으로도 열거는 실용성이 없어진다.

익명 업로드는 불법 콘텐츠 유통 경로가 될 수 있다. IP 일일 쿼터, 레이트리밋,
실행 파일 경고, 신고 창구는 선택이 아니라 운영 조건이다.
