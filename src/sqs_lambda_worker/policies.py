"""IAM policy documents as plain functions (easy to read and to test).

Each function returns the smallest set of actions the worker needs, on one
resource ARN. Nothing here uses ``"Resource": "*"``.
"""

from __future__ import annotations

import json
from typing import Any

PolicyDocument = dict[str, Any]


def _document(*statements: dict[str, Any]) -> PolicyDocument:
    return {"Version": "2012-10-17", "Statement": list(statements)}


def to_json(document: PolicyDocument) -> str:
    return json.dumps(document, sort_keys=True)


def lambda_trust() -> PolicyDocument:
    return _document(
        {
            "Effect": "Allow",
            "Principal": {"Service": "lambda.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }
    )


def sqs_consume(queue_arn: str) -> PolicyDocument:
    """What the event source mapping needs to poll the queue for the function."""
    return _document(
        {
            "Sid": "ConsumeWorkQueue",
            "Effect": "Allow",
            "Action": [
                "sqs:ReceiveMessage",
                "sqs:DeleteMessage",
                "sqs:ChangeMessageVisibility",
                "sqs:GetQueueAttributes",
            ],
            "Resource": queue_arn,
        }
    )


def sqs_send(queue_arn: str) -> PolicyDocument:
    """Lets the function move poison messages to the DLQ itself."""
    return _document(
        {
            "Sid": "SendToDeadLetterQueue",
            "Effect": "Allow",
            "Action": "sqs:SendMessage",
            "Resource": queue_arn,
        }
    )


def logs_write(log_group_arn: str) -> PolicyDocument:
    """Write to one log group only (instead of the broad AWS managed policy)."""
    return _document(
        {
            "Sid": "WriteOwnLogGroup",
            "Effect": "Allow",
            "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
            "Resource": f"{log_group_arn}:*",
        }
    )


def s3_prefix_read_write(bucket_arn: str, prefix: str) -> PolicyDocument:
    """Read and write objects under one prefix.

    ``s3:ListBucket`` (limited to the prefix) is there on purpose: without it,
    HeadObject on a missing key returns 403 instead of 404.
    """
    prefix = prefix.lstrip("/")
    return _document(
        {
            "Sid": "ReadWriteOutputPrefix",
            "Effect": "Allow",
            "Action": ["s3:GetObject", "s3:PutObject", "s3:AbortMultipartUpload"],
            "Resource": f"{bucket_arn}/{prefix}*",
        },
        {
            "Sid": "ListOutputPrefix",
            "Effect": "Allow",
            "Action": "s3:ListBucket",
            "Resource": bucket_arn,
            "Condition": {"StringLike": {"s3:prefix": [f"{prefix}*"]}},
        },
    )
