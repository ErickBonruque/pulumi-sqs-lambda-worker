"""``SqsLambdaWorker``: a queue, its DLQ and a container Lambda that consumes it.

Use it for any job that fits "one message = one unit of work"::

    worker = SqsLambdaWorker(
        "thumbnails",
        WorkerArgs(
            image_uri=image.ref,
            environment={"OUTPUT_BUCKET": bucket.bucket},
            extra_policies={"s3": bucket.arn.apply(lambda arn: to_json(s3_policy(arn)))},
        ),
    )

The numbers follow the AWS guidance for SQS event sources:

* queue visibility timeout = 6 x function timeout + batching window, so a
  message is not delivered twice while a slow invocation still runs;
* ``ReportBatchItemFailures``: only the failed messages of a batch come back;
* ``maximum_concurrency`` on the mapping (not reserved concurrency on the
  function), so SQS stops polling instead of getting throttled, and throttled
  messages do not use up their receive count and land in the DLQ;
* DLQ retention of 14 days (the maximum), with an alarm when it is not empty.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

import pulumi
import pulumi_aws as aws

from sqs_lambda_worker import policies
from sqs_lambda_worker.tags import governance_tags

_DLQ_RETENTION_SECONDS = 14 * 24 * 60 * 60


@dataclass
class WorkerArgs:
    image_uri: pulumi.Input[str]
    environment: Mapping[str, pulumi.Input[str]] = field(default_factory=dict)
    extra_policies: Mapping[str, pulumi.Input[str]] = field(default_factory=dict)
    """Inline IAM policies (JSON) for what the job itself touches, by name."""
    timeout_seconds: int = 30
    memory_mb: int = 512
    architecture: Literal["x86_64", "arm64"] = "x86_64"
    batch_size: int = 5
    batching_window_seconds: int = 0
    max_concurrency: int = 5
    max_receive_count: int = 3
    log_retention_days: int = 30
    alarm_actions: Sequence[pulumi.Input[str]] = ()

    def __post_init__(self) -> None:
        if not 1 <= self.timeout_seconds <= 900:
            raise ValueError("timeout_seconds must be between 1 and 900")
        if not 2 <= self.max_concurrency <= 1000:
            raise ValueError("max_concurrency must be between 2 and 1000 (AWS limit)")
        if not 1 <= self.batch_size <= 10_000:
            raise ValueError("batch_size must be between 1 and 10000")
        if self.batch_size > 10 and self.batching_window_seconds < 1:
            raise ValueError("batch_size above 10 needs batching_window_seconds >= 1")
        if self.max_receive_count < 1:
            raise ValueError("max_receive_count must be at least 1")

    @property
    def visibility_timeout_seconds(self) -> int:
        return 6 * self.timeout_seconds + self.batching_window_seconds


class SqsLambdaWorker(pulumi.ComponentResource):
    queue: aws.sqs.Queue
    dead_letter_queue: aws.sqs.Queue
    function: aws.lambda_.Function
    role: aws.iam.Role
    log_group: aws.cloudwatch.LogGroup
    event_source_mapping: aws.lambda_.EventSourceMapping
    dlq_alarm: aws.cloudwatch.MetricAlarm

    def __init__(
        self, name: str, args: WorkerArgs, opts: pulumi.ResourceOptions | None = None
    ) -> None:
        super().__init__("sqs-lambda-worker:index:SqsLambdaWorker", name, None, opts)
        self._prefix = name
        child = pulumi.ResourceOptions(parent=self)
        tags = governance_tags()

        self.dead_letter_queue = aws.sqs.Queue(
            f"{name}-dlq",
            message_retention_seconds=_DLQ_RETENTION_SECONDS,
            sqs_managed_sse_enabled=True,
            tags=tags,
            opts=child,
        )
        self.queue = aws.sqs.Queue(
            f"{name}-queue",
            visibility_timeout_seconds=args.visibility_timeout_seconds,
            sqs_managed_sse_enabled=True,
            redrive_policy=self.dead_letter_queue.arn.apply(
                lambda arn: policies.to_json(
                    {"deadLetterTargetArn": arn, "maxReceiveCount": args.max_receive_count}
                )
            ),
            tags=tags,
            opts=child,
        )
        # Only our queue may use this DLQ.
        aws.sqs.RedriveAllowPolicy(
            f"{name}-dlq-allow",
            queue_url=self.dead_letter_queue.url,
            redrive_allow_policy=self.queue.arn.apply(
                lambda arn: policies.to_json(
                    {"redrivePermission": "byQueue", "sourceQueueArns": [arn]}
                )
            ),
            opts=child,
        )

        self.log_group = aws.cloudwatch.LogGroup(
            f"{name}-logs",
            retention_in_days=args.log_retention_days,
            tags=tags,
            opts=child,
        )

        self.role = aws.iam.Role(
            f"{name}-role",
            assume_role_policy=policies.to_json(policies.lambda_trust()),
            tags=tags,
            opts=child,
        )
        logs_policy = self._allow(
            "logs", self.log_group.arn.apply(lambda a: policies.to_json(policies.logs_write(a)))
        )
        consume_policy = self._allow(
            "consume", self.queue.arn.apply(lambda a: policies.to_json(policies.sqs_consume(a)))
        )
        role_policies = [
            logs_policy,
            consume_policy,
            self._allow(
                "dlq-send",
                self.dead_letter_queue.arn.apply(lambda a: policies.to_json(policies.sqs_send(a))),
            ),
            *(self._allow(key, doc) for key, doc in args.extra_policies.items()),
        ]

        self.function = aws.lambda_.Function(
            f"{name}-fn",
            package_type="Image",
            image_uri=args.image_uri,
            architectures=[args.architecture],
            role=self.role.arn,
            timeout=args.timeout_seconds,
            memory_size=args.memory_mb,
            environment=aws.lambda_.FunctionEnvironmentArgs(
                variables={**args.environment, "DLQ_URL": self.dead_letter_queue.url},
            ),
            logging_config=aws.lambda_.FunctionLoggingConfigArgs(
                log_format="JSON",
                log_group=self.log_group.name,
                application_log_level="INFO",
                system_log_level="WARN",
            ),
            tags=tags,
            opts=pulumi.ResourceOptions(parent=self, depends_on=[logs_policy]),
        )

        self.event_source_mapping = aws.lambda_.EventSourceMapping(
            f"{name}-mapping",
            event_source_arn=self.queue.arn,
            function_name=self.function.arn,
            batch_size=args.batch_size,
            maximum_batching_window_in_seconds=args.batching_window_seconds,
            function_response_types=["ReportBatchItemFailures"],
            scaling_config=aws.lambda_.EventSourceMappingScalingConfigArgs(
                maximum_concurrency=args.max_concurrency,
            ),
            tags=tags,
            # AWS checks that the role can read the queue when it creates the mapping,
            # and the first messages must not arrive before the job's own permissions.
            opts=pulumi.ResourceOptions(parent=self, depends_on=role_policies),
        )

        self.dlq_alarm = aws.cloudwatch.MetricAlarm(
            f"{name}-dlq-not-empty",
            alarm_description="Messages in the dead-letter queue need attention.",
            namespace="AWS/SQS",
            metric_name="ApproximateNumberOfMessagesVisible",
            dimensions={"QueueName": self.dead_letter_queue.name},
            statistic="Maximum",
            period=300,
            evaluation_periods=1,
            threshold=0,
            comparison_operator="GreaterThanThreshold",
            treat_missing_data="notBreaching",
            alarm_actions=list(args.alarm_actions),
            tags=tags,
            opts=child,
        )

        self.register_outputs(
            {
                "queue_url": self.queue.url,
                "dead_letter_queue_url": self.dead_letter_queue.url,
                "function_name": self.function.name,
            }
        )

    def _allow(self, name: str, policy_json: pulumi.Input[str]) -> aws.iam.RolePolicy:
        return aws.iam.RolePolicy(
            f"{self._prefix}-{name}",
            role=self.role.id,
            policy=policy_json,
            opts=pulumi.ResourceOptions(parent=self),
        )
