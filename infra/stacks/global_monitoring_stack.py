"""us-east-1 에만 둘 수 있는 모니터링.

CloudFront 메트릭은 리전이 없는 글로벌 서비스지만 **CloudWatch 에는 us-east-1 로만
올라온다.** 그리고 CloudWatch 알람은 자기가 감시하는 메트릭과 같은 리전에 있어야
한다. 그래서 서비스 본체가 ap-northeast-2 에 있어도 이 알람만은 us-east-1 에
따로 두어야 한다. (ap-northeast-2 에서 만들려고 하면 CDK 가 합성 단계에서
``AlarmRegionMismatch`` 로 거부한다.)

전송량 급증은 이 서비스에서 **가장 중요한 운영 신호**다. 익명 업로드 서비스가
남용당하면 스토리지보다 전송비가 훨씬 먼저, 훨씬 빠르게 오른다. 예산 알람은
하루 단위로 평가되어 늦으므로 시간 단위 알람이 따로 필요하다.
"""

from aws_cdk import Duration, Stack
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subs
from constructs import Construct

#: 시간당 이 양을 넘으면 남용을 의심한다.
#: CloudFront 프리티어가 월 1TB 이므로, 이 속도가 하루만 지속돼도 프리티어의
#: 4분의 1을 쓰게 된다.
EGRESS_ALARM_BYTES_PER_HOUR = 10 * 1024**3


class GlobalMonitoringStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        distribution_id: str,
        alert_email: str = "",
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)  # type: ignore[arg-type]

        # SNS 토픽도 알람과 같은 리전에 있어야 한다.
        # ap-northeast-2 의 토픽을 알람 액션으로 걸 수 없다.
        topic = sns.Topic(self, "GlobalAlertTopic", display_name="Dropship 전송량 알림")
        if alert_email:
            topic.add_subscription(subs.EmailSubscription(alert_email))

        alarm = cloudwatch.Alarm(
            self,
            "EgressSpikeAlarm",
            metric=cloudwatch.Metric(
                namespace="AWS/CloudFront",
                metric_name="BytesDownloaded",
                dimensions_map={
                    "DistributionId": distribution_id,
                    # CloudFront 메트릭의 Region 차원 값은 리전 이름이 아니라
                    # 항상 문자열 "Global" 이다.
                    "Region": "Global",
                },
                statistic="Sum",
                period=Duration.hours(1),
            ),
            threshold=EGRESS_ALARM_BYTES_PER_HOUR,
            evaluation_periods=1,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            alarm_description=(
                "CloudFront 시간당 전송량이 10GB를 넘었다 — 익명 업로드 남용 가능성"
            ),
        )
        alarm.add_alarm_action(cw_actions.SnsAction(topic))
