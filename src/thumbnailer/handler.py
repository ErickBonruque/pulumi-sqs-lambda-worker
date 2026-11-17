"""AWS Lambda entry point for SQS batches.

The event source mapping uses ``ReportBatchItemFailures``: the handler returns
the IDs of the messages that failed, and SQS retries only those. Permanent
failures are sent straight to the DLQ (with the reason) instead of being
retried ``maxReceiveCount`` times for nothing.
"""

from __future__ import annotations

import logging
from functools import cache
from typing import TYPE_CHECKING, Any

import boto3

from thumbnailer.config import Settings
from thumbnailer.errors import PermanentError
from thumbnailer.messages import ThumbnailRequest
from thumbnailer.storage import S3Store
from thumbnailer.worker import process

if TYPE_CHECKING:
    from mypy_boto3_sqs import SQSClient

logger = logging.getLogger("thumbnailer")
logger.setLevel(logging.INFO)


@cache
def _settings() -> Settings:
    return Settings.from_env()


@cache
def _store() -> S3Store:
    # Local runs point boto3 at an emulator with AWS_ENDPOINT_URL; nothing to do here.
    settings = _settings()
    return S3Store(boto3.client("s3"), settings.bucket, settings.output_prefix)


@cache
def _sqs() -> SQSClient:
    return boto3.client("sqs")


def send_to_dlq(record: dict[str, Any], reason: str, settings: Settings) -> bool:
    """Copy a message to the DLQ. Return False when no DLQ is configured."""
    if not settings.dlq_url:
        return False
    _sqs().send_message(
        QueueUrl=settings.dlq_url,
        MessageBody=record.get("body", ""),
        MessageAttributes={
            "error": {"DataType": "String", "StringValue": reason[:1000]},
            "source_message_id": {"DataType": "String", "StringValue": record["messageId"]},
        },
    )
    return True


def handle_record(record: dict[str, Any], settings: Settings) -> bool:
    """Process one SQS record. Return True when the message can be deleted."""
    message_id = record["messageId"]
    try:
        request = ThumbnailRequest.from_body(record.get("body", ""))
        result = process(request, _store(), settings.fetch, settings.render)
    except PermanentError as exc:
        reason = f"{type(exc).__name__}: {exc}"
        logger.warning("permanent failure", extra={"message_id": message_id, "reason": reason})
        try:
            return send_to_dlq(record, reason, settings)
        except Exception:
            logger.exception("could not send to the DLQ", extra={"message_id": message_id})
            return False
    except Exception:
        logger.exception("transient failure, SQS will retry", extra={"message_id": message_id})
        return False

    logger.info(
        "skipped, already done" if result.skipped else "processed",
        extra={"message_id": message_id, "image_id": result.image_id},
    )
    return True


def handler(event: dict[str, Any], context: object) -> dict[str, list[dict[str, str]]]:
    settings = _settings()
    failures = [
        {"itemIdentifier": record["messageId"]}
        for record in event.get("Records", [])
        if not handle_record(record, settings)
    ]
    return {"batchItemFailures": failures}
