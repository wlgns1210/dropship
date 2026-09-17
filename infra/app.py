#!/usr/bin/env python
"""CDK 앱 진입점.

    cd infra
    cdk deploy SkiffStack

비밀값은 저장소에 두지 않는다. ``ORIGIN_SECRET`` 과 ``IP_HASH_SALT`` 를 환경
변수로 넘겨야 하며, 없으면 배포를 거부한다(임시값으로 조용히 배포되는 것이
가장 나쁜 결과라서). 값 생성은 ``python scripts/gen_secrets.py`` 로 한다.
"""

import os
import sys

import aws_cdk as cdk

from stacks.cicd_stack import CicdStack
from stacks.skiff_stack import SkiffStack
from stacks.global_monitoring_stack import GlobalMonitoringStack

app = cdk.App()

env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=os.environ.get("CDK_DEFAULT_REGION", "ap-northeast-2"),
)


def required_secret(name: str) -> str:
    value = os.environ.get(name, "")
    if len(value) < 32:
        sys.exit(
            f"환경 변수 {name} 가 없거나 너무 짧습니다(32자 이상 필요).\n"
            f"  python scripts/gen_secrets.py  로 생성한 뒤 환경 변수로 넣어 주세요.\n"
            f"  CI 에서는 GitHub 저장소 시크릿에 등록합니다."
        )
    return value


# 알람 수신 주소는 환경 변수를 우선한다. 저장소가 공개라 cdk.json 에 개인
# 이메일을 적어둘 수 없기 때문이다. 비어 있으면 SNS 구독과 예산 알림을 만들지
# 않는다(알람 자체는 만들어지되 메일이 가지 않는다).
alert_email = os.environ.get("ALERT_EMAIL") or app.node.try_get_context("alertEmail") or ""

main = SkiffStack(
    app,
    "SkiffStack",
    env=env,
    description="익명 파일 전송 서비스 — 링크와 QR로 보내고 기간이 지나면 사라진다",
    origin_secret=required_secret("ORIGIN_SECRET"),
    ip_hash_salt=required_secret("IP_HASH_SALT"),
    alert_email=alert_email,
    monthly_budget_usd=int(app.node.try_get_context("monthlyBudgetUsd") or 5),
    enable_waf=bool(app.node.try_get_context("enableWaf")),
    # 전송량 알람 스택이 다른 리전에서 distribution_id 를 참조한다.
    # CDK 가 SSM 파라미터와 커스텀 리소스로 리전 간 참조를 이어준다.
    cross_region_references=True,
)

# CloudFront 메트릭은 us-east-1 에만 올라오고 알람은 메트릭과 같은 리전에 있어야
# 한다. 서비스는 서울에 있지만 이 알람만 버지니아에 둔다.
GlobalMonitoringStack(
    app,
    "SkiffGlobalMonitoringStack",
    env=cdk.Environment(account=env.account, region="us-east-1"),
    description="CloudFront 전송량 알람 (us-east-1 고정)",
    distribution_id=main.distribution_id,
    alert_email=alert_email,
    cross_region_references=True,
)

# GitHub Actions 배포용 IAM 역할. githubRepo 컨텍스트가 있을 때만 만든다.
# 앱 스택과 수명주기가 다르므로(한 번 만들고 거의 안 건드림) 따로 둔다.
github_repo = app.node.try_get_context("githubRepo") or ""
if github_repo:
    CicdStack(
        app,
        "SkiffCicdStack",
        env=env,
        description="GitHub Actions OIDC 배포 역할",
        github_repo=github_repo,
    )

app.synth()
