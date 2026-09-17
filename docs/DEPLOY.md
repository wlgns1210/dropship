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
| 파일 개수 | 100개 (2개 이상이면 ZIP 일괄 받기) |
| 보관 기간 | 1시간 / 6시간 / 24시간(기본) / 3일 / 7일 |
| IP 일일 업로드 한도 | 5GB |
| 디스크 최소 여유 | 2GB (미만이면 503) |
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
echo 'export ALERT_EMAIL=you@example.com' >> ~/.skiff-secrets
source ~/.skiff-secrets
```

### `NEXT_PUBLIC_API_BASE_URL=""` 를 빠뜨리지 말 것

로컬 개발용 `http://localhost:8000` 이 번들에 박히면 배포본이 통째로 죽는다.
브라우저가 자기 PC 의 8000 포트로 API 를 호출하게 된다. 빌드 후 확인:

```bash
grep -rl "localhost:8000" apps/web/out | wc -l   # 0 이어야 한다
```

## 다른 VM 에 컨테이너로 배포

이미지는 main 에 푸시될 때마다 GitHub Container Registry 로 올라간다
(`.github/workflows/image.yml`).

```
ghcr.io/<owner>/skiff-api:latest
ghcr.io/<owner>/skiff-web:latest
```

### 배포할 VM 에서

저장소를 받을 필요 없다. **파일 두 개면 된다.**

#### Ubuntu (22.04 / 24.04)

```bash
# 1. Docker 설치 — 공식 저장소를 쓴다
sudo apt-get update
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc]   https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable"   | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io   docker-buildx-plugin docker-compose-plugin

sudo systemctl enable --now docker
sudo usermod -aG docker "$USER" && newgrp docker
```

> **`apt install docker.io` 로 설치하지 말 것.** 우분투 기본 저장소의 그 패키지에는
> `docker compose`(v2 플러그인)가 들어 있지 않다. 이 프로젝트의 compose 파일은 v2
> 문법이라 `docker-compose`(v1)로는 뜨지 않는다. 공식 저장소의
> `docker-compose-plugin` 이 있어야 한다.

```bash
# 2. compose 파일과 환경 파일만 받는다
curl -O https://raw.githubusercontent.com/<owner>/skiff/main/compose.deploy.yaml
curl -o .env https://raw.githubusercontent.com/<owner>/skiff/main/docker/env.example

# 3. 비밀값을 채운다
python3 -c "import secrets;print('IP_HASH_SALT='+secrets.token_urlsafe(48))"
python3 -c "import secrets;print('URL_SIGNING_KEY='+secrets.token_urlsafe(48))"
python3 -c "import secrets;print('ADMIN_TOKEN='+secrets.token_urlsafe(32))"
# 출력을 .env 에 넣고 SKIFF_REGISTRY 를 자기 소유자로 맞춘다
chmod 600 .env

# 4. 기동
docker compose -f compose.deploy.yaml up -d
docker compose -f compose.deploy.yaml ps
```

#### ⚠️ 우분투에서 반드시 짚어야 할 것 — Docker 는 ufw 를 우회한다

우분투에는 보통 `ufw` 가 있고, 많은 사람이 이렇게 해두고 안전하다고 믿는다.

```bash
sudo ufw default deny incoming
sudo ufw allow 22/tcp
sudo ufw enable
```

**그런데 `ports:` 로 공개한 컨테이너 포트는 이 규칙을 무시하고 인터넷에서 그대로
열린다.** Docker 가 자기 iptables 규칙을 ufw 보다 앞선 체인(DOCKER-USER)에
넣기 때문이다. 익명 업로드 서비스라 이 차이가 실제 피해로 이어진다 — 막았다고
생각한 포트로 아무나 파일을 올린다.

두 가지 중 하나를 택한다.

**A. 앞단 프록시만 외부에 노출한다** (권장)

컨테이너를 루프백에만 묶고, 호스트의 nginx·Caddy 가 TLS 를 끝내며 프록시한다.

```bash
# .env
SKIFF_PORT=127.0.0.1:8090
```

`ports: - "127.0.0.1:8090:80"` 으로 해석되어 외부에서 직접 닿을 수 없다.
이러면 ufw 규칙도 의미를 되찾는다.

**B. DOCKER-USER 체인에 직접 막는다**

```bash
sudo iptables -I DOCKER-USER -p tcp --dport 8090 ! -s 10.0.0.0/8 -j DROP
```

재부팅하면 사라지므로 `iptables-persistent` 로 저장해야 한다. A 가 더 단순하다.

#### Amazon Linux 2023

```bash
sudo dnf install -y docker
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER" && newgrp docker
```

나머지 절차(2~4번)는 우분투와 같다. AL2023 에는 ufw 가 없고 보안 그룹이
그 역할을 하는데, **보안 그룹은 Docker 우회 문제가 없다** — 인스턴스 밖에서
거르기 때문이다.

### 이미지가 private 이면

ghcr.io 패키지는 저장소가 공개여도 **기본이 private** 이다. 두 가지 중 하나를
택한다.

**A. 패키지를 공개로 바꾼다** (간단하고, 포트폴리오라면 이쪽)

GitHub → 프로필 → Packages → `skiff-api` → Package settings →
Change visibility → Public. `skiff-web` 도 같이. 그러면 VM 에서 인증 없이
`docker pull` 이 된다.

**B. VM 에서 로그인한다** (비공개로 두고 싶을 때)

```bash
# GitHub 에서 read:packages 권한만 가진 토큰을 만든다
echo "<TOKEN>" | docker login ghcr.io -u <owner> --password-stdin
```

### 갱신

```bash
docker compose -f compose.deploy.yaml pull
docker compose -f compose.deploy.yaml up -d
```

`pull_policy: always` 라서 `up -d` 만으로도 최신을 받는다. 특정 버전으로
고정하려면 `.env` 에 `SKIFF_TAG=v1.2.3` 처럼 넣는다 — 운영에서는 `latest`
보다 태그 고정이 안전하다.

### 반드시 알아야 할 것

**TLS 가 없다.** 이 스택은 80 포트에 평문으로 뜬다. 앞단에 인증서를 끝내는
무언가(로드밸런서·Caddy·nginx·Cloudflare)가 반드시 있어야 한다. 평문으로
공개하면 파일이 그대로 오가고, `navigator.clipboard` 가 막혀 링크 복사
버튼도 동작하지 않는다.

**데이터는 볼륨에 있다.** `docker compose down` 은 안전하지만 `-v` 를 붙이면
업로드된 파일과 모든 공유 링크가 사라진다.

**`URL_SIGNING_KEY` 를 바꾸면 이미 발급된 링크가 즉시 죽는다.** VM 을 옮길 때
이 값을 그대로 가져가야 기존 링크가 살아남는다.

## GitHub Actions CI/CD

`.github/workflows/deploy.yml` 이 main 푸시마다 배포한다. 장기 액세스 키 대신
OIDC 로 매 실행마다 임시 자격증명을 받는다.

### 준비

```bash
# OIDC 역할 생성 (한 번만)
cd infra
cdk deploy SkiffCicdStack -c githubRepo=<owner>/<repo>
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
