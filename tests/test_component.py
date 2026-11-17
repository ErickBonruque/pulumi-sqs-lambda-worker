"""Unit tests for the Pulumi component, using Pulumi's mock engine (no cloud calls)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pulumi
import pytest

ACCOUNT = "123456789012"


@dataclass
class Created:
    typ: str
    name: str
    inputs: dict[str, Any]


class Mocks(pulumi.runtime.Mocks):
    def __init__(self) -> None:
        self.resources: list[Created] = []

    def new_resource(self, args: pulumi.runtime.MockResourceArgs) -> tuple[str, dict[str, Any]]:
        self.resources.append(Created(args.typ, args.name, dict(args.inputs)))
        outputs = dict(args.inputs)
        service = args.typ.split(":")[1].split("/")[0]
        outputs.setdefault("arn", f"arn:aws:{service}:us-east-1:{ACCOUNT}:{args.name}")
        outputs.setdefault("name", args.name)
        if args.typ == "aws:sqs/queue:Queue":
            outputs["url"] = f"https://sqs.us-east-1.amazonaws.com/{ACCOUNT}/{args.name}"
        return f"{args.name}-id", outputs

    def call(
        self, args: pulumi.runtime.MockCallArgs
    ) -> tuple[dict[Any, Any], list[tuple[str, str]] | None]:
        return {}, None

    def of_type(self, typ: str) -> list[Created]:
        return [r for r in self.resources if r.typ == typ]

    def one(self, typ: str, name_part: str = "") -> Created:
        [match] = [r for r in self.of_type(typ) if name_part in r.name]
        return match


MOCKS = Mocks()
pulumi.runtime.set_mocks(MOCKS, project="test-project", stack="dev", preview=False)

from sqs_lambda_worker import SqsLambdaWorker, WorkerArgs  # noqa: E402

TAGGABLE = {
    "aws:sqs/queue:Queue",
    "aws:iam/role:Role",
    "aws:lambda/function:Function",
    "aws:lambda/eventSourceMapping:EventSourceMapping",
    "aws:cloudwatch/logGroup:LogGroup",
    "aws:cloudwatch/metricAlarm:MetricAlarm",
}


def build(args: WorkerArgs) -> pulumi.Output[Any]:
    """Create the worker and resolve once every child resource is registered."""
    MOCKS.resources.clear()
    worker = SqsLambdaWorker("jobs", args)
    children = [
        worker.queue,
        worker.dead_letter_queue,
        worker.function,
        worker.role,
        worker.log_group,
        worker.event_source_mapping,
        worker.dlq_alarm,
    ]
    return pulumi.Output.all(*(child.urn for child in children))


ARGS = WorkerArgs(
    image_uri=f"{ACCOUNT}.dkr.ecr.us-east-1.amazonaws.com/worker@sha256:abc",
    environment={"OUTPUT_BUCKET": "outputs"},
    extra_policies={"s3": json.dumps({"Version": "2012-10-17", "Statement": []})},
    timeout_seconds=40,
    batching_window_seconds=5,
)


@pulumi.runtime.test
def test_queue_timeouts_and_redrive() -> pulumi.Output[Any]:
    def check(_: Any) -> None:
        queue = MOCKS.one("aws:sqs/queue:Queue", "jobs-queue")
        dlq = MOCKS.one("aws:sqs/queue:Queue", "jobs-dlq")
        assert queue.inputs["visibilityTimeoutSeconds"] == 6 * 40 + 5
        redrive = json.loads(queue.inputs["redrivePolicy"])
        assert redrive["maxReceiveCount"] == 3
        assert redrive["deadLetterTargetArn"].endswith(":jobs-dlq")
        assert dlq.inputs["messageRetentionSeconds"] == 14 * 24 * 3600
        assert queue.inputs["sqsManagedSseEnabled"] and dlq.inputs["sqsManagedSseEnabled"]
        allow = json.loads(
            MOCKS.one("aws:sqs/redriveAllowPolicy:RedriveAllowPolicy").inputs["redriveAllowPolicy"]
        )
        assert allow == {"redrivePermission": "byQueue", "sourceQueueArns": [redrive_source(queue)]}

    return build(ARGS).apply(check)


def redrive_source(queue: Created) -> str:
    return f"arn:aws:sqs:us-east-1:{ACCOUNT}:{queue.name}"


@pulumi.runtime.test
def test_function_and_event_source_mapping() -> pulumi.Output[Any]:
    def check(_: Any) -> None:
        function = MOCKS.one("aws:lambda/function:Function")
        assert function.inputs["packageType"] == "Image"
        assert function.inputs["timeout"] == 40
        variables = function.inputs["environment"]["variables"]
        assert variables["OUTPUT_BUCKET"] == "outputs"
        assert variables["DLQ_URL"].endswith("/jobs-dlq")
        assert function.inputs["loggingConfig"]["logFormat"] == "JSON"

        mapping = MOCKS.one("aws:lambda/eventSourceMapping:EventSourceMapping")
        assert mapping.inputs["functionResponseTypes"] == ["ReportBatchItemFailures"]
        assert mapping.inputs["scalingConfig"]["maximumConcurrency"] == 5
        assert mapping.inputs["maximumBatchingWindowInSeconds"] == 5

    return build(ARGS).apply(check)


@pulumi.runtime.test
def test_role_gets_one_inline_policy_per_need() -> pulumi.Output[Any]:
    def check(_: Any) -> None:
        names = sorted(p.name for p in MOCKS.of_type("aws:iam/rolePolicy:RolePolicy"))
        assert names == ["jobs-consume", "jobs-dlq-send", "jobs-logs", "jobs-s3"]
        assert not MOCKS.of_type("aws:iam/rolePolicyAttachment:RolePolicyAttachment")

    return build(ARGS).apply(check)


@pulumi.runtime.test
def test_dlq_alarm_watches_the_dlq() -> pulumi.Output[Any]:
    def check(_: Any) -> None:
        alarm = MOCKS.one("aws:cloudwatch/metricAlarm:MetricAlarm")
        assert alarm.inputs["dimensions"] == {"QueueName": "jobs-dlq"}
        assert alarm.inputs["threshold"] == 0
        assert alarm.inputs["comparisonOperator"] == "GreaterThanThreshold"

    return build(ARGS).apply(check)


@pulumi.runtime.test
def test_every_taggable_resource_has_governance_tags() -> pulumi.Output[Any]:
    def check(_: Any) -> None:
        tagged = [r for r in MOCKS.resources if r.typ in TAGGABLE]
        assert len(tagged) == 7
        for resource in tagged:
            tags = resource.inputs["tags"]
            assert tags["ManagedBy"] == "pulumi", resource.name
            assert tags["Project"] == "test-project"
            assert tags["Environment"] == "nonprod"

    return build(ARGS).apply(check)


@pytest.mark.parametrize(
    "changes",
    [
        {"timeout_seconds": 0},
        {"timeout_seconds": 901},
        {"max_concurrency": 1},
        {"batch_size": 50},
        {"max_receive_count": 0},
    ],
)
def test_invalid_args_fail_early(changes: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        WorkerArgs(image_uri="x", **changes)  # type: ignore[arg-type]
