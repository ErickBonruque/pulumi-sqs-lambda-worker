from __future__ import annotations

import json
from typing import Any

import pytest

from sqs_lambda_worker import policies

QUEUE = "arn:aws:sqs:us-east-1:123456789012:work"
LOGS = "arn:aws:logs:us-east-1:123456789012:log-group:worker"
BUCKET = "arn:aws:s3:::outputs"


def statements(document: dict[str, Any]) -> list[dict[str, Any]]:
    assert document["Version"] == "2012-10-17"
    return list(document["Statement"])


@pytest.mark.parametrize(
    "document",
    [
        policies.sqs_consume(QUEUE),
        policies.sqs_send(QUEUE),
        policies.logs_write(LOGS),
        policies.s3_prefix_read_write(BUCKET, "images/"),
    ],
)
def test_no_wildcard_actions_or_resources(document: dict[str, Any]) -> None:
    for statement in statements(document):
        actions = statement["Action"]
        assert all(
            "*" not in action for action in ([actions] if isinstance(actions, str) else actions)
        )
        assert statement["Resource"] != "*"


def test_consume_has_exactly_what_the_event_source_mapping_needs() -> None:
    [statement] = statements(policies.sqs_consume(QUEUE))
    assert sorted(statement["Action"]) == [
        "sqs:ChangeMessageVisibility",
        "sqs:DeleteMessage",
        "sqs:GetQueueAttributes",
        "sqs:ReceiveMessage",
    ]
    assert statement["Resource"] == QUEUE


def test_logs_are_limited_to_one_log_group() -> None:
    [statement] = statements(policies.logs_write(LOGS))
    assert statement["Resource"] == LOGS + ":*"
    assert "logs:CreateLogGroup" not in statement["Action"]


def test_s3_access_is_limited_to_the_prefix() -> None:
    objects, listing = statements(policies.s3_prefix_read_write(BUCKET, "/images/"))
    assert objects["Resource"] == "arn:aws:s3:::outputs/images/*"
    assert "s3:DeleteObject" not in objects["Action"]
    assert listing["Action"] == "s3:ListBucket"
    assert listing["Condition"] == {"StringLike": {"s3:prefix": ["images/*"]}}


def test_trust_policy_allows_only_lambda() -> None:
    [statement] = statements(policies.lambda_trust())
    assert statement["Principal"] == {"Service": "lambda.amazonaws.com"}


def test_to_json_is_stable() -> None:
    document = policies.sqs_send(QUEUE)
    assert policies.to_json(document) == policies.to_json(json.loads(policies.to_json(document)))
