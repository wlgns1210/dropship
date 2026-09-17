# 배포

지금 운영되는 구성은 **단일 VM + 컨테이너 3개**다. 이미지는 ghcr.io 에서 받는다.

저장소에는 AWS 서버리스 경로(`infra/` CDK)도 함께 남아 있다. 앱이 저장소·스토리지를
인터페이스 뒤에 두고 있어 `DEPLOY_MODE` 한 값으로 갈리기 때문이다. 그쪽은 문서
아래쪽 [대안: AWS 서버리스](#대안-aws-서버리스)에 따로 적었다.

## 구성

```
  사용자
    │
    │   앞단에서 TLS 종단 (로드밸런서 · 게이트웨이 · Cloudflare) — 선택
    ▼
  ┌──────────────────┐
  │  web  (nginx)    │   /*           정적 파일 (Next.js 내보내기)
  │                  │   /api/*       api 로 프록시
  │                  │   /protected/  internal — 파일 직송
  └────────┬─────────┘
           │
           ▼
  ┌──────────────────┐
  │  api  (FastAPI)  │   uvicorn
  └────────┬─────────┘
           │
           ▼
  ┌──────────────────┐
  │  volume          │   skiff.db   SQLite (WAL)
  │  skiff_data      │   files/     업로드된 파일
  └────────▲─────────┘
           │
  ┌────────┴─────────┐
  │  sweeper         │   5분마다 만료분 삭제
  └──────────────────┘
```

**다운로드할 때 파일 바이트는 Python 을 지나지 않는다.** API 는 토큰만 확인하고
`X-Accel-Redirect` 헤더를 돌려주며, 실제 전송은 nginx 가 `sendfile` 로 한다.
그래서 web 컨테이너도 같은 볼륨을 읽기 전용으로 마운트한다 — 이 마운트를
빠뜨리면 화면은 멀쩡히 뜨는데 다운로드만 404 가 난다.

업로드는 반대로 API 를 지난다. 다만 파트 단위로 스트리밍해 디스크에 바로 쓰므로
파일 크기와 메모리 사용량은 무관하다.

세 컨테이너가 `skiff_data` 볼륨 하나를 공유하므로 **api 와 web 이미지의 GID 를
10001 로 맞춰 두었다.** 업로드 파일이 `0640` 이라 같은 그룹이어야 nginx 가 읽는다.

## 정책 값

| 항목 | 값 | 상수 |
|---|---|---|
| 전송당 크기 상한 | 1GB | `MAX_TOTAL_BYTES` |
| 파일 개수 | 100개 (2개 이상이면 ZIP 일괄 받기) | `MAX_FILES` |
| 보관 기간 | 1시간 / 6시간 / 24시간(기본) / 3일 / 7일 | `EXPIRY_CHOICES` |
| 다운로드 횟수 | 제한 없음 (기간 내 몇 번이든) | — |
| IP 일일 업로드 한도 | 5GB | `DAILY_QUOTA_BYTES` |
| 디스크 최소 여유 | 2GB (미만이면 503) | `DISK_HEADROOM_BYTES` |
| 다운로드 서명 URL 수명 | 5분 | `DOWNLOAD_URL_TTL` |
| 멀티파트 파트 크기 | 8MiB | `PART_SIZE` |
| 미완 업로드 정리 | 1일 | `PENDING_TTL_SECONDS` |
| Sweeper 주기 | 5분 | `SWEEP_INTERVAL_SECONDS` |

레이트리밋(`RATE_LIMITS`)은 IP 해시 기준이다.

| 스코프 | 한도 |
|---|---|
| 업로드 세션 생성 | 10회 / 60초 |
| 공유 코드 조회 | 100회 / 300초 |
| 다운로드 | 60회 / 300초 |

전부 `apps/api/app/config.py` 한 곳에 모여 있다.

## 배포할 VM 에서

저장소를 받을 필요 없다. **파일 두 개면 된다.**

아래 절차는 빈 디렉터리에서 그대로 따라 실행해 확인했다 — 스모크 11개와
실제 12MiB 왕복(업로드 · 다운로드 · 한글 파일명 · 소유자 삭제)이 통과한다.

이미지는 main 에 푸시될 때마다 GitHub Container Registry 로 올라간다
(`.github/workflows/image.yml`).

```
ghcr.io/<owner>/skiff-api:latest
ghcr.io/<owner>/skiff-web:latest
```

### Ubuntu (22.04 / 24.04)

```bash
# 1. Docker 설치 — 공식 저장소를 쓴다
sudo apt-get update
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin

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

### `.env` 에 들어가는 값

| 이름 | 필수 | 설명 |
|---|---|---|
| `IP_HASH_SALT` | 예 | 레이트리밋·쿼터가 쓰는 IP 해시의 솔트. 바꾸면 그날 쿼터 집계가 초기화된다 |
| `URL_SIGNING_KEY` | 예 | 업로드·다운로드 토큰 서명 키. **바꾸면 이미 발급된 링크가 즉시 죽는다** |
| `ADMIN_TOKEN` | 아니오 | 비우면 `/api/admin/*` 라우터가 **아예 등록되지 않는다** |
| `SKIFF_REGISTRY` | 아니오 | 기본 `ghcr.io/wlgns1210` |
| `SKIFF_TAG` | 아니오 | 기본 `latest`. 운영은 태그 고정이 안전하다 |
| `SKIFF_PORT` | 아니오 | 기본 `8090`. 호스트의 어느 주소·포트에 붙일지 |
| `SWEEP_INTERVAL_SECONDS` | 아니오 | 기본 `300` |

`SKIFF_PORT` 는 `8090` 처럼 포트만 쓰면 `0.0.0.0:8090` 에 붙고,
`127.0.0.1:8090` 처럼 주소를 붙이면 루프백에만 붙는다. 앞단 프록시를 쓸 때는
후자를 쓴다 — 바로 아래 ufw 항목을 보라.

### ⚠️ 우분투에서 반드시 짚어야 할 것 — Docker 는 ufw 를 우회한다

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

### Amazon Linux 2023

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

## 배포 후 확인할 것

**상태 코드만 보면 안 된다.** 이 서비스는 매칭되지 않는 경로를 전부 `404.html`
로 돌려주면서 200 을 낸다(공유 링크를 살리기 위한 설계다). 그래서 "200 이 왔다"
는 페이지가 제대로 떴다는 증거가 되지 못한다 — 실제로 `/admin` 이 404 화면을
200 으로 돌려주는데도 점검을 통과한 적이 있다.

본문까지 확인하는 스모크 스크립트가 있다. 저장소를 받지 않는 배포라
이것도 따로 내려받는다.

```bash
curl -O https://raw.githubusercontent.com/<owner>/skiff/main/deploy/verify.sh
bash verify.sh http://<주소>
```

11개 항목이 전부 OK 여야 한다.

업로드부터 삭제까지 실제로 왕복시키려면 (저장소가 있는 곳에서, `httpx` 필요):

```bash
SKIFF_API=http://<주소> python scripts/e2e_check.py
```

12MiB 실멀티파트 업로드, 한글 파일명 복원, 열거 방어, 소유자 전용 삭제까지 본다.

## 갱신

```bash
docker compose -f compose.deploy.yaml pull
docker compose -f compose.deploy.yaml up -d
```

`pull_policy: always` 라서 `up -d` 만으로도 최신을 받는다. 특정 버전으로
고정하려면 `.env` 에 `SKIFF_TAG=v1.2.3` 처럼 넣는다 — 운영에서는 `latest`
보다 태그 고정이 안전하다.

## 반드시 알아야 할 것

**TLS 가 없다.** 이 스택은 80 포트에 평문으로 뜬다. 앞단에 인증서를 끝내는
무언가(로드밸런서·Caddy·nginx·Cloudflare)가 반드시 있어야 한다. 평문으로
공개하면 파일이 그대로 오간다. `navigator.clipboard` 도 보안 컨텍스트에서만
동작해서 막히는데, 이쪽은 `window.prompt` 로 떨어져 링크를 직접 선택할 수는
있다 — 버튼이 죽지는 않지만 한 단계 불편해진다.

**데이터는 볼륨에 있다.** `docker compose down` 은 안전하지만 `-v` 를 붙이면
업로드된 파일과 모든 공유 링크가 사라진다.

**백업이 없다.** `skiff_data` 볼륨이 사라지면 끝이다. 보관 기간이 최대 7일이라
손실 범위는 제한적이지만, 없다는 사실은 알고 있어야 한다.

**`URL_SIGNING_KEY` 를 바꾸면 이미 발급된 링크가 즉시 죽는다.** VM 을 옮길 때
이 값을 그대로 가져가야 기존 링크가 살아남는다.

**관리자 화면은 `ADMIN_TOKEN` 이 있을 때만 존재한다.** 비워 두면 라우터 자체가
등록되지 않아 `/api/admin/*` 이 404 다. 공개 서비스에 붙는 화면이라 "인증으로
막는다" 보다 "존재하지 않는다" 가 안전하다.

## 앞단에 프록시를 둘 때

`X-Forwarded-For` 를 어떻게 다루는지가 중요하다. 레이트리밋과 일일 쿼터가
이 값으로 사람을 가르기 때문이다.

앱은 XFF 의 **마지막** 값을 쓴다. 첫 값을 쓰면 `X-Forwarded-For: 1.2.3.4` 를
보내는 것만으로 매 요청 다른 신원을 꾸며내 방어가 통째로 무력화된다. 가장 가까운
신뢰 프록시가 덧붙인 마지막 값만 믿을 수 있다.

따라서 **앞단이 XFF 에 실제 클라이언트 IP 를 넣어도 앱은 그 값을 쓰지 않는다.**
우리 nginx 가 그 뒤에 자기가 본 주소를 덧붙이기 때문이다. 결과는 이렇게 갈린다.

- 앞단이 **NAT 포워더**면 — 모든 사용자가 앞단의 주소 하나로 보인다.
  레이트리밋과 일일 쿼터를 전체가 공유한다. 위조는 막히지만 한도는 거칠어진다.
- 앞단이 **HTTP 리버스 프록시**여도 마찬가지다. 실제 IP 별로 가르려면 신뢰 홉
  수를 세어 뒤에서 N+1 번째를 고르는 설정이 필요한데, 지금은 없다.

거칠어도 안전한 쪽을 택한 결과다. 홉 수를 잘못 세면 위조가 다시 열린다.

## 바깥에서 접속이 안 될 때

`curl localhost` 는 되는데 도메인으로는 안 되는 상황이 가장 흔하고, 가장
헷갈린다. 컨테이너가 멀쩡하니 로그에도 아무것도 안 남는다 — **요청이 아예
도착하지 않기 때문이다.** 순서대로 좁힌다.

```bash
# ① 이 VM 이 실제로 쓰는 공인 IP 와 도메인이 가리키는 곳이 같은가
echo "VM   : $(curl -s4 ifconfig.me)"
echo "도메인: $(getent hosts <도메인> | awk '{print $1}')"

# ② 80·443 을 이미 누가 잡고 있지 않은가
ss -tlnp | grep -E ':(80|443|2019)'
```

`127.0.0.1:2019` 가 보이면 **Caddy 가 돌고 있다.** 예전에 깔아둔 Caddy 가
80/443 을 쥔 채 인증서를 못 받으면, 80 은 https 로 308 을 보내고 443 은
핸드셰이크에서 죽는다. 그 상태가 "서버가 아예 없는 것" 처럼 보인다.

**포트 번호가 그대로 전달된다고 가정하지 말 것.** 학교·사내 게이트웨이 뒤에
있으면 외부 포트와 VM 포트가 다르게 매핑된다. 실제로 겪은 예:

| 외부 | 도착지 |
|------|--------|
| `25133` | VM 의 **80** |
| `24133` | VM 의 **22** (SSH) |
| `80`·`443` | 게이트웨이 자신 (VM 까지 오지 않음) |

이걸 모르고 `SKIFF_PORT=25133` 으로 두면 컨테이너는 VM 의 25133 에 붙는데
외부 25133 은 VM 의 80 으로 들어와, 아무도 컨테이너에 닿지 못한다.

**매핑을 확정하는 방법**은 앞단을 잠깐 멈춰보는 것이다. 멈췄을 때 응답이
사라지는 포트가 이 VM 까지 오는 포트다.

```bash
sudo systemctl stop caddy      # 다른 창에서 바깥에서 curl
```

그리고 **ACME 는 이런 환경에서 성공할 수 없다.** Let's Encrypt 는 도메인이
가리키는 IP 의 80(HTTP-01) 또는 443(TLS-ALPN-01)으로만 검증하는데, 그 두
포트가 게이트웨이에서 끝나면 VM 까지 오지 않는다. 포트를 바꿔 우회할 수 없다.
DNS-01 챌린지를 쓰거나, 게이트웨이가 TLS 를 끝내주거나, 터널(Cloudflare
Tunnel 등)을 써야 한다.

---

# 대안: AWS 서버리스

`DEPLOY_MODE=aws` 로 도는 경로다. 저장소·스토리지가 인터페이스 뒤에 있어
라우터는 어느 쪽인지 모른 채 돌아간다. 두 구현을 모두 유지하는 이유이기도 하다.

**지금 운영에는 쓰고 있지 않다.** 위의 컨테이너 배포가 현재 구성이다.

리전 `ap-northeast-2` (+ CloudFront 알람만 `us-east-1`).
도메인 없이 CloudFront 기본 도메인을 쓴다.

```
  사용자
    │
    ▼
  ┌──────────────┐
  │  CloudFront  │
  └──┬────────┬──┘
     │        │
     │ /*     │ /api/*
     ▼        ▼
  ┌──────┐  ┌──────────────┐
  │  S3  │  │  Lambda URL  │   FastAPI (Mangum)
  └──────┘  └──┬───────────┘
   웹 정적      │
                ├───> DynamoDB    메타데이터
                └───> S3          파일 — 브라우저가 presigned URL 로 직접 업/다운로드

  EventBridge (5분) ───> Sweeper Lambda ───> 만료분 삭제
  us-east-1              CloudFront 전송량 알람 (메트릭이 이 리전에만 존재)
```

컨테이너 배포와 결정적으로 다른 점은 파일 경로다. 이쪽은 브라우저가 S3
presigned URL 로 **직접** 올리고 직접 받아서, 업로드조차 애플리케이션을
지나지 않는다.

이쪽에서만 의미가 있는 값:

| 항목 | 값 |
|---|---|
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

### 오리진 시크릿이 없으면 방어가 통째로 열린다

Lambda 함수 URL 은 공개 주소다. CloudFront 가 붙이는 `ORIGIN_SECRET` 헤더가
없는 요청은 403 으로 거부한다. 이게 없으면 주소를 알아낸 사람이 CloudFront 를
건너뛰고 직접 호출해 `X-Forwarded-For` 를 마음대로 꾸밀 수 있고, 그러면
레이트리밋과 쿼터가 통째로 우회된다.

같은 이유로 `CloudFront-Viewer-Address` 헤더는 **CloudFront 뒤에 있을 때만**
읽는다. 단일 노드 구성에서 그 헤더를 믿으면 클라이언트가 직접 채워 보낼 수 있다.

## GitHub Actions 자동 배포

`.github/workflows/deploy.yml` 이 담당한다. 장기 액세스 키 대신 OIDC 로 매
실행마다 임시 자격증명을 받는다.

**지금은 push 트리거가 주석 처리되어 있다.** OIDC 역할과 시크릿이 준비되기
전에 main 에 푸시하면 매번 빨간 X 만 남기 때문이다. 아래를 마친 뒤 주석을 푼다.

```bash
# OIDC 역할 생성 (한 번만)
cd infra
cdk deploy SkiffCicdStack -c githubRepo=<owner>/<repo>
```

Settings → Secrets and variables → Actions 에 등록한다.

| 이름 | 값 |
|---|---|
| `AWS_ACCOUNT_ID` | AWS 계정 번호 |
| `ORIGIN_SECRET` | `gen_secrets.py` 가 만든 값 |
| `IP_HASH_SALT` | `gen_secrets.py` 가 만든 값 |

`IP_HASH_SALT` 는 **한 번 정하면 바꾸지 않는다.** 바꾸면 진행 중인 일일 쿼터
집계가 초기화되어 그날 한도를 다 쓴 사람도 다시 올릴 수 있게 된다.

컨테이너 이미지 빌드(`image.yml`)와는 별개다. 그쪽은 main 푸시마다 돌고 있다.

## 배포 후 확인

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

앱 레벨 레이트리밋(조회 100회/5분)이 1차 방어선이다. 코드 공간이 약 3억이라
열거 공격은 이 제한만으로도 실용성이 없어진다. 트래픽이 늘거나 실제 공격
징후가 보이면 `infra/cdk.json` 의 `enableWaf` 를 켜고 재배포한다.

### 전체 삭제

```bash
cd infra && cdk destroy --all
```

S3 버킷은 `auto_delete_objects=True` 라 안의 파일까지 함께 지워진다.
실제 운영이라면 `RemovalPolicy.RETAIN` 으로 바꿔야 한다.
