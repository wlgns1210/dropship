"""Dropship 운영 인프라.

    CloudFront ─┬─ /api/*  → Lambda 함수 URL (FastAPI + Mangum)
                └─ /*      → S3 (Next.js 정적 산출물)

    EventBridge(5분) → Sweeper Lambda → 만료 객체 삭제

설계상 눈여겨볼 것
------------------
**공유 URL 을 서버가 만들지 않는다.** 만들게 하면 Lambda 환경 변수에 CloudFront
도메인을 넣어야 하는데, CloudFront 는 그 Lambda 의 함수 URL 을 오리진으로 삼으므로
순환 의존이 된다(Lambda→env→Distribution→FunctionUrl→Lambda). 브라우저가
``location.origin`` 으로 만들면 이 문제가 아예 없다.

**Lambda 함수 URL 은 공개 주소다.** CloudFront 가 붙이는 시크릿 헤더가 없으면
거부하도록 앱이 검사한다. 이게 없으면 주소를 알아낸 사람이 CloudFront 를 건너뛰고
``X-Forwarded-For`` 를 위조해 레이트리밋과 일일 쿼터를 통째로 우회할 수 있다.
"""

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
)
from aws_cdk import (
    aws_budgets as budgets,
)
from aws_cdk import (
    aws_cloudfront as cloudfront,
)
from aws_cdk import (
    aws_cloudfront_origins as origins,
)
from aws_cdk import (
    aws_cloudwatch as cloudwatch,
)
from aws_cdk import (
    aws_cloudwatch_actions as cw_actions,
)
from aws_cdk import (
    aws_dynamodb as dynamodb,
)
from aws_cdk import (
    aws_events as events,
)
from aws_cdk import (
    aws_events_targets as targets,
)
from aws_cdk import (
    aws_lambda as lambda_,
)
from aws_cdk import (
    aws_s3 as s3,
)
from aws_cdk import (
    aws_s3_deployment as s3deploy,
)
from aws_cdk import (
    aws_sns as sns,
)
from aws_cdk import (
    aws_sns_subscriptions as subs,
)
from constructs import Construct

#: 오리진 시크릿을 실어 보내는 헤더. apps/api/app/config.py 의 값과 같아야 한다.
ORIGIN_SECRET_HEADER = "x-dropship-origin"

#: 업로더가 고를 수 있는 최대 보관 기간(7일)보다 넉넉히 잡은 안전망.
#: Sweeper 가 계속 실패해도 객체가 영원히 남지는 않게 한다.
SAFETY_NET_DAYS = 30


class DropshipStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        origin_secret: str,
        ip_hash_salt: str,
        alert_email: str = "",
        monthly_budget_usd: int = 5,
        enable_waf: bool = False,
        web_asset_path: str = "../apps/web/out",
        api_asset_path: str = "../build/lambda",
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)  # type: ignore[arg-type]

        files_bucket = self._files_bucket()
        web_bucket = self._web_bucket()
        table = self._table()

        api_fn, api_url = self._api_function(
            files_bucket, table, origin_secret, ip_hash_salt, api_asset_path
        )
        self._sweeper(files_bucket, table, api_asset_path)

        distribution = self._distribution(web_bucket, api_url, origin_secret, enable_waf)

        # 정적 산출물 업로드 + CloudFront 캐시 무효화
        s3deploy.BucketDeployment(
            self,
            "WebDeployment",
            sources=[s3deploy.Source.asset(web_asset_path)],
            destination_bucket=web_bucket,
            distribution=distribution,
            distribution_paths=["/*"],
            prune=True,
        )

        # 브라우저가 S3 로 직접 PUT 하려면 버킷 CORS 가 CloudFront 도메인을 허용해야
        # 한다. 도메인은 배포 후에야 알 수 있고 버킷은 그 전에 만들어지므로,
        # 여기서는 오리진을 특정하지 않는다. 업로드 URL 자체가 서명되어 있어
        # 시간 제한 + 키 고정이라 오리진 제한은 실질 방어선이 아니다.
        # ExposeHeaders 의 ETag 가 없으면 멀티파트를 닫을 수 없다(브라우저가
        # CORS 응답에서 못 읽는다).
        files_bucket.add_cors_rule(
            allowed_methods=[s3.HttpMethods.PUT, s3.HttpMethods.GET, s3.HttpMethods.HEAD],
            allowed_origins=["*"],
            allowed_headers=["*"],
            exposed_headers=["ETag"],
            max_age=3000,
        )

        self._monitoring(api_fn, alert_email, monthly_budget_usd)

        # us-east-1 모니터링 스택이 참조한다.
        self.distribution_id = distribution.distribution_id
        self.site_url = f"https://{distribution.distribution_domain_name}"

        # 출력 ID 는 같은 스택의 construct ID 와 겹치면 안 된다
        # (CfnOutput 도 construct 라 같은 이름 공간을 쓴다).
        CfnOutput(self, "SiteUrl", value=f"https://{distribution.distribution_domain_name}")
        CfnOutput(self, "CfDistributionId", value=distribution.distribution_id)
        CfnOutput(self, "FilesBucketName", value=files_bucket.bucket_name)
        CfnOutput(self, "WebBucketName", value=web_bucket.bucket_name)
        CfnOutput(self, "DataTableName", value=table.table_name)

    # ── 저장소 ────────────────────────────────────────────────

    def _files_bucket(self) -> s3.Bucket:
        bucket = s3.Bucket(
            self,
            "FilesBucket",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            # 포트폴리오 환경이라 스택을 지우면 파일도 같이 지운다.
            # 실제 운영이라면 RETAIN 으로 두고 수동 정리해야 한다.
            auto_delete_objects=True,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="abort-abandoned-multipart",
                    # 브라우저가 업로드 도중 탭을 닫으면 멀티파트가 열린 채 남는다.
                    # 이 조각들은 목록에 보이지 않으면서 보관료가 나가므로 반드시
                    # 정리해야 한다.
                    abort_incomplete_multipart_upload_after=Duration.days(1),
                    enabled=True,
                ),
                s3.LifecycleRule(
                    id="safety-net-expiry",
                    # Sweeper 가 계속 실패해도 객체가 영원히 남지 않게 하는 백스톱.
                    # 정상 경로에서는 여기까지 오기 전에 삭제된다.
                    expiration=Duration.days(SAFETY_NET_DAYS),
                    enabled=True,
                ),
            ],
        )
        return bucket

    def _web_bucket(self) -> s3.Bucket:
        return s3.Bucket(
            self,
            "WebBucket",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

    def _table(self) -> dynamodb.TableV2:
        return dynamodb.TableV2(
            self,
            "Table",
            partition_key=dynamodb.Attribute(name="PK", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="SK", type=dynamodb.AttributeType.STRING),
            billing=dynamodb.Billing.on_demand(),
            # TTL 은 만료 수단이 아니라 Sweeper 실패 시의 백스톱이다.
            # 실제 삭제까지 최대 48시간이 걸리므로 이것만 믿으면 안 된다.
            time_to_live_attribute="ttl",
            removal_policy=RemovalPolicy.DESTROY,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=False
            ),
            global_secondary_indexes=[
                dynamodb.GlobalSecondaryIndexPropsV2(
                    index_name="GSI1",
                    partition_key=dynamodb.Attribute(
                        name="GSI1PK", type=dynamodb.AttributeType.STRING
                    ),
                    sort_key=dynamodb.Attribute(
                        name="GSI1SK", type=dynamodb.AttributeType.NUMBER
                    ),
                )
            ],
        )

    # ── 컴퓨트 ────────────────────────────────────────────────

    def _lambda_env(
        self, files_bucket: s3.Bucket, table: dynamodb.TableV2, ip_hash_salt: str
    ) -> dict[str, str]:
        return {
            "DROPSHIP_ENV": "production",
            # AWS_ENDPOINT_URL 은 아예 넣지 않는다. botocore 도 이 이름의 환경
            # 변수를 읽기 때문에, 빈 문자열을 넣으면 "설정됐지만 비어 있음" 으로
            # 해석될 여지를 만든다. 없으면 기본 해석 경로를 그대로 탄다.
            "S3_BUCKET": files_bucket.bucket_name,
            "DYNAMODB_TABLE": table.table_name,
            "IP_HASH_SALT": ip_hash_salt,
            "DOWNLOAD_SIGNER": "local",
            # DOWNLOAD_SIGNER=local 은 S3 presigned GET 을 쓴다는 뜻이다.
            # CloudFront 서명 URL 로 바꾸려면 키페어(유료 기능 아님이지만 수동
            # 생성 필요)를 만들고 cloudfront 로 바꾸면 된다. S3 presigned 는
            # CloudFront 를 거치지 않아 전송비 캐싱 이점이 없는 대신 설정이 없다.
        }

    def _api_function(
        self,
        files_bucket: s3.Bucket,
        table: dynamodb.TableV2,
        origin_secret: str,
        ip_hash_salt: str,
        asset_path: str,
    ) -> tuple[lambda_.Function, lambda_.FunctionUrl]:
        fn = lambda_.Function(
            self,
            "ApiFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.X86_64,
            handler="app.main.lambda_handler",
            code=lambda_.Code.from_asset(asset_path),
            memory_size=512,
            timeout=Duration.seconds(30),
            environment={
                **self._lambda_env(files_bucket, table, ip_hash_salt),
                "ORIGIN_SECRET": origin_secret,
            },
        )
        files_bucket.grant_read_write(fn)
        files_bucket.grant_delete(fn)
        table.grant_read_write_data(fn)

        url = fn.add_function_url(auth_type=lambda_.FunctionUrlAuthType.NONE)
        return fn, url

    def _sweeper(
        self, files_bucket: s3.Bucket, table: dynamodb.TableV2, asset_path: str
    ) -> lambda_.Function:
        fn = lambda_.Function(
            self,
            "SweeperFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.X86_64,
            handler="app.sweeper.lambda_handler",
            code=lambda_.Code.from_asset(asset_path),
            memory_size=256,
            timeout=Duration.minutes(2),
            environment=self._lambda_env(files_bucket, table, "unused-by-sweeper"),
        )
        files_bucket.grant_read_write(fn)
        files_bucket.grant_delete(fn)
        table.grant_read_write_data(fn)

        # 이것이 파일이 실제로 사라지는 주 경로다. DynamoDB TTL 과 S3 라이프사이클은
        # 이게 실패했을 때를 위한 백스톱일 뿐이다.
        events.Rule(
            self,
            "SweeperSchedule",
            schedule=events.Schedule.rate(Duration.minutes(5)),
            targets=[targets.LambdaFunction(fn)],
        )
        return fn

    # ── 배포 ──────────────────────────────────────────────────

    def _distribution(
        self,
        web_bucket: s3.Bucket,
        api_url: lambda_.FunctionUrl,
        origin_secret: str,
        enable_waf: bool,
    ) -> cloudfront.Distribution:
        api_origin = origins.FunctionUrlOrigin(
            api_url,
            custom_headers={ORIGIN_SECRET_HEADER: origin_secret},
        )

        distribution = cloudfront.Distribution(
            self,
            "Distribution",
            default_root_object="index.html",
            price_class=cloudfront.PriceClass.PRICE_CLASS_200,  # 아시아 포함, 남미 제외
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(web_bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
                compress=True,
            ),
            additional_behaviors={
                "/api/*": cloudfront.BehaviorOptions(
                    origin=api_origin,
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                    # ALL_VIEWER_EXCEPT_HOST_HEADER 여야 한다. Host 를 그대로 넘기면
                    # Lambda 함수 URL 이 자기 도메인이 아닌 Host 를 받고 거부한다.
                    # 이 정책은 CloudFront-Viewer-Address 도 함께 넘겨주는데,
                    # 앱이 레이트리밋 기준 IP 로 쓰는 값이라 반드시 필요하다.
                    origin_request_policy=(
                        cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER
                    ),
                    compress=True,
                ),
            },
            error_responses=[
                # 공유 링크(/oslo/113245)는 S3 에 없는 키다. 404.html 을 200 으로
                # 돌려주면 Next 앱 셸이 떠서 클라이언트가 경로를 읽고 수신 화면을
                # 그린다. 이것이 정적 배포에서 예쁜 URL 을 쓰는 방법이다.
                #
                # OAC 를 쓰면 S3 는 없는 키에 403 을 준다(ListBucket 권한이 없어서).
                # 그래서 403 과 404 를 모두 매핑해야 한다.
                cloudfront.ErrorResponse(
                    http_status=403,
                    response_http_status=200,
                    response_page_path="/404.html",
                    ttl=Duration.seconds(0),
                ),
                cloudfront.ErrorResponse(
                    http_status=404,
                    response_http_status=200,
                    response_page_path="/404.html",
                    ttl=Duration.seconds(0),
                ),
            ],
        )

        if enable_waf:
            distribution.node.add_metadata("waf", "enabled")

        return distribution

    # ── 모니터링 ──────────────────────────────────────────────

    def _monitoring(
        self,
        api_fn: lambda_.Function,
        alert_email: str,
        monthly_budget_usd: int,
    ) -> None:
        topic = sns.Topic(self, "AlertTopic", display_name="Dropship 알림")
        if alert_email:
            topic.add_subscription(subs.EmailSubscription(alert_email))

        action = cw_actions.SnsAction(topic)

        cloudwatch.Alarm(
            self,
            "ApiErrorAlarm",
            metric=api_fn.metric_errors(period=Duration.minutes(5)),
            threshold=5,
            evaluation_periods=1,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            alarm_description="API Lambda 오류가 5분에 5건을 넘었다",
        ).add_alarm_action(action)

        cloudwatch.Alarm(
            self,
            "ApiThrottleAlarm",
            metric=api_fn.metric_throttles(period=Duration.minutes(5)),
            threshold=1,
            evaluation_periods=1,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            alarm_description="API Lambda 가 스로틀되고 있다 — 동시성 한도 확인 필요",
        ).add_alarm_action(action)

        # 전송량 급증 알람은 여기 없다. CloudFront 메트릭은 us-east-1 에만
        # 올라오고 CloudWatch 알람은 메트릭과 같은 리전에 있어야 하므로,
        # GlobalMonitoringStack(us-east-1)이 대신 만든다.

        if alert_email:
            budgets.CfnBudget(
                self,
                "MonthlyBudget",
                budget=budgets.CfnBudget.BudgetDataProperty(
                    budget_type="COST",
                    time_unit="MONTHLY",
                    budget_limit=budgets.CfnBudget.SpendProperty(
                        amount=monthly_budget_usd, unit="USD"
                    ),
                ),
                notifications_with_subscribers=[
                    budgets.CfnBudget.NotificationWithSubscribersProperty(
                        notification=budgets.CfnBudget.NotificationProperty(
                            comparison_operator="GREATER_THAN",
                            notification_type="FORECASTED",
                            threshold=100,
                            threshold_type="PERCENTAGE",
                        ),
                        subscribers=[
                            budgets.CfnBudget.SubscriberProperty(
                                address=alert_email, subscription_type="EMAIL"
                            )
                        ],
                    )
                ],
            )
