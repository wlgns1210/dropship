"""GitHub Actions 가 AWS 에 배포할 수 있게 하는 OIDC 역할.

장기 액세스 키(``AWS_ACCESS_KEY_ID`` / ``AWS_SECRET_ACCESS_KEY``)를 GitHub
시크릿에 넣는 방식은 쓰지 않는다. 키가 유출되면 취소할 때까지 무제한으로
쓰이기 때문이다. OIDC 는 워크플로 실행마다 15분짜리 임시 자격증명을 받는다.

신뢰 조건을 저장소와 브랜치까지 좁히는 것이 핵심이다. ``repo:*`` 로 열어두면
GitHub 의 **아무 저장소나** 이 역할을 맡을 수 있다.
"""

from aws_cdk import Stack
from aws_cdk import aws_iam as iam
from constructs import Construct

GITHUB_OIDC_URL = "https://token.actions.githubusercontent.com"


class CicdStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        github_repo: str,
        deploy_branch: str = "main",
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)  # type: ignore[arg-type]

        if "/" not in github_repo:
            raise ValueError(f"githubRepo 는 'owner/repo' 형식이어야 합니다: {github_repo!r}")

        # 계정에 이미 GitHub OIDC 공급자가 있으면 그것을 쓴다.
        # 공급자는 계정당 하나뿐이라 중복 생성하면 배포가 실패한다.
        provider = iam.OpenIdConnectProvider(
            self,
            "GithubOidcProvider",
            url=GITHUB_OIDC_URL,
            client_ids=["sts.amazonaws.com"],
        )

        role = iam.Role(
            self,
            "GithubDeployRole",
            role_name="skiff-github-deploy",
            description=f"{github_repo} 의 {deploy_branch} 브랜치가 맡는 배포 역할",
            max_session_duration=Stack.of(self).node.try_get_context("maxSession") or None,
            assumed_by=iam.WebIdentityPrincipal(
                provider.open_id_connect_provider_arn,
                {
                    "StringEquals": {
                        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                    },
                    # 이 한 줄이 방어의 전부다. 저장소와 브랜치를 고정하지 않으면
                    # 누구의 GitHub 워크플로든 이 역할을 맡을 수 있다.
                    "StringLike": {
                        "token.actions.githubusercontent.com:sub": (
                            f"repo:{github_repo}:ref:refs/heads/{deploy_branch}"
                        ),
                    },
                },
            ),
        )

        # CDK 는 부트스트랩이 만든 cdk-*-deploy-role 등을 맡아서 배포한다.
        # 그래서 이 역할에 필요한 권한은 "그 역할들을 맡을 수 있는 것" 뿐이고,
        # 서비스 권한을 직접 넓게 줄 필요가 없다.
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["sts:AssumeRole"],
                resources=[f"arn:aws:iam::{self.account}:role/cdk-*"],
            )
        )
        # cdk diff / deploy 가 부트스트랩 버전을 조회할 때 쓴다.
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["ssm:GetParameter"],
                resources=[f"arn:aws:ssm:{self.region}:{self.account}:parameter/cdk-bootstrap/*"],
            )
        )

        self.role = role
