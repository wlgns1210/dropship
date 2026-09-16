# 배포

리전 `ap-northeast-2` (+ CloudFront 알람만 `us-east-1`).
도메인 없이 CloudFront 기본 도메인을 쓴다.

## 구성

```
                 ┌──────────────┐
   사용자 ─────> │  CloudFront  │
                 └──────┬───────┘
         /api/*         │        /*
   ┌─────────────┐      │   ┌──────────────┐
   │ Lambda URL  │<─────┴──>│ S3 (웹 정적)  │
   │ FastAPI     │          └──────────────┘
   └──────┬──────┘
          │          ┌──────────────┐
          ├─────────>│  DynamoDB    │
          │          └──────────────┘
          └─────────>┌──────────────┐
                     │ S3 (파일)     │<── 브라우저 직접 업/다운로드
                     └──────┬───────┘
  EventBridge(5분) → Sweeper Lambda ┘

  us-east-1: CloudFront 전송량 알람 (메트릭이 이 리전에만 존재)
```

## 정책 값

| 항목 | 값 |
|---|---|
| 전송당 크기 상한 | 1GB |
| 파일 개수 | 100개 |
| 보관 기간 | 1시간 / 6시간 / 24시간(기본) / 3일 / 7일 |
| IP 일일 업로드 한도 | 5GB |
| 다운로드 서명 URL 수명 | 5분 |
| Sweeper 주기 | 5분 |
| S3 안전망 만료 | 30일 |
| 미완 멀티파트 정리 | 1일 |
| 예산 알람 | $5 (예상치 100% 도달 시) |
| 전송량 알람 | 시간당 10GB |

## 처음 배포할 때

비밀값과 알람 수신 주소는 **저장소에 두지 않는다.** 이 저장소는 공개라
`cdk.json` 에 적으면 그대로 노출된다. 전부 환경 변수로 넘긴다.

```bash
# 1. 시크릿 생성 → 안전한 곳에 보관 (커밋 금지)
python scripts/gen_secrets.py

# 2. CDK 부트스트랩 — 두 리전 모두 필요하다
cdk bootstrap aws://<계정>/ap-northeast-2
cdk bootstrap aws://<계정>/us-east-1

# 3. 빌드
NEXT_PUBLIC_API_BASE_URL="" npm run build --workspace apps/web
python scripts/build_lambda.py

# 4. 배포
cd infra
export ORIGIN_SECRET=... IP_HASH_SALT=...
export ALERT_EMAIL=you@example.com    # 비우면 알람 메일이 가지 않는다
cdk deploy --all
```

### `ALERT_EMAIL` 없이 재배포하면 알람 구독이 사라진다

CDK 는 선언된 것과 실제를 맞추므로, 이 값이 비어 있으면 기존 SNS 구독과 예산
알림을 **삭제한다.** 배포할 때마다 넣어야 한다. 서버에 두고 쓰려면:

```bash
echo 'export ALERT_EMAIL=you@example.com' >> ~/.dropship-secrets
source ~/.dropship-secrets
```

### `NEXT_PUBLIC_API_BASE_URL=""` 를 빠뜨리지 말 것

로컬 개발용 `http://localhost:8000` 이 번들에 박히면 배포본이 통째로 죽는다.
브라우저가 자기 PC 의 8000 포트로 API 를 호출하게 된다. 빌드 후 확인:

```bash
grep -rl "localhost:8000" apps/web/out | wc -l   # 0 이어야 한다
```

## GitHub Actions CI/CD

`.github/workflows/deploy.yml` 이 main 푸시마다 배포한다. 장기 액세스 키 대신
OIDC 로 매 실행마다 임시 자격증명을 받는다.

### 준비

```bash
# OIDC 역할 생성 (한 번만)
cd infra
cdk deploy DropshipCicdStack -c githubRepo=<owner>/<repo>
```

### GitHub 저장소 시크릿

Settings → Secrets and variables → Actions 에 등록한다.

| 이름 | 값 |
|---|---|
| `AWS_ACCOUNT_ID` | AWS 계정 번호 |
| `ORIGIN_SECRET` | `gen_secrets.py` 가 만든 값 |
| `IP_HASH_SALT` | `gen_secrets.py` 가 만든 값 |

`IP_HASH_SALT` 는 **한 번 정하면 바꾸지 않는다.** 바꾸면 진행 중인 일일 쿼터
집계가 초기화되어 그날 한도를 다 쓴 사람도 다시 올릴 수 있게 된다.

## 배포 후 확인할 것

1. **SNS 구독 확인 메일 2통** (서울 · 버지니아 각 1통) — 클릭해야 알람이 온다.
   확인 전에는 알람이 울려도 메일이 가지 않는다.
2. `GET /api/health` → 200
3. `/` → 업로드 화면
4. `/oslo/123456` → 404 가 아니라 **200 + 앱 셸** (없는 링크 안내 화면이 뜨면 정상)
5. 실제 파일 업로드 → 링크 → 다른 기기에서 다운로드

## 운영 중 알아둘 것

### 비용의 거의 전부는 CloudFront 전송비다

CloudFront 프리티어는 월 1TB 전송 + 1000만 요청이다. 정상 사용이면 사실상 $0 다.
$5 예산 알람이 울린다면 정상 사용량을 넘은 것이므로 남용을 의심해야 한다.

### 전송량 알람이 울리면

1. CloudFront 로그에서 어떤 코드가 반복 다운로드되는지 확인
2. 해당 전송을 DynamoDB 에서 삭제 (Sweeper 가 S3 객체도 정리한다)
3. 지속되면 `enableWaf: true` 로 WAF 를 켠다 (월 약 $10)

### WAF 는 꺼져 있다

앱 레벨 레이트리밋(DynamoDB 기반, 조회 100회/5분)이 1차 방어선이다. 코드 공간이
약 3억이라 열거 공격은 이 제한만으로도 실용성이 없어진다. 트래픽이 늘거나
실제 공격 징후가 보이면 `infra/cdk.json` 의 `enableWaf` 를 켜고 재배포한다.

### 전체 삭제

```bash
cd infra && cdk destroy --all
```

S3 버킷은 `auto_delete_objects=True` 라 안의 파일까지 함께 지워진다.
실제 운영이라면 `RemovalPolicy.RETAIN` 으로 바꿔야 한다.
