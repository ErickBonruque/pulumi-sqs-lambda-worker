"""The Lambda handler against S3 and SQS emulated by moto (no AWS account)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from moto import mock_aws

from tests.conftest import ImageFactory
from thumbnailer import handler as lambda_handler
from thumbnailer.errors import TransientFetchError
from thumbnailer.fetch import FetchPolicy

BUCKET = "thumbnails-test"
URL = "https://upload.wikimedia.org/wikipedia/commons/a/a9/Example.jpg"


def clear_caches() -> None:
    for cached in (lambda_handler._settings, lambda_handler._store, lambda_handler._sqs):
        cached.cache_clear()


@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Moto S3 + SQS, env vars as Pulumi sets them. Yields the DLQ URL."""
    for name, value in {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_DEFAULT_REGION": "us-east-1",
        "OUTPUT_BUCKET": BUCKET,
        "OUTPUT_PREFIX": "images/",
        "THUMBNAIL_WIDTHS": "64,128",
    }.items():
        monkeypatch.setenv(name, value)
    with mock_aws():
        boto3.client("s3").create_bucket(Bucket=BUCKET)
        dlq_url = boto3.client("sqs").create_queue(QueueName="worker-dlq")["QueueUrl"]
        monkeypatch.setenv("DLQ_URL", dlq_url)
        clear_caches()
        yield dlq_url
    clear_caches()


def record(message_id: str, body: str) -> dict[str, Any]:
    return {"messageId": message_id, "body": body, "eventSource": "aws:sqs"}


def event(*records: dict[str, Any]) -> dict[str, Any]:
    return {"Records": list(records)}


def ok_body(url: str = URL, image_id: str = "example") -> str:
    return json.dumps({"source_url": url, "image_id": image_id})


def serve(monkeypatch: pytest.MonkeyPatch, data: bytes | Exception) -> list[str]:
    calls: list[str] = []

    def fake_fetch(url: str, policy: FetchPolicy) -> bytes:
        calls.append(url)
        if isinstance(data, Exception):
            raise data
        return data

    monkeypatch.setattr("thumbnailer.worker.fetch_image", fake_fetch)
    return calls


def dlq_messages(dlq_url: str) -> list[Any]:
    response = boto3.client("sqs").receive_message(
        QueueUrl=dlq_url, MaxNumberOfMessages=10, MessageAttributeNames=["All"]
    )
    return list(response.get("Messages", []))


def test_success_writes_objects_to_s3(
    aws: str, monkeypatch: pytest.MonkeyPatch, image_bytes: ImageFactory
) -> None:
    serve(monkeypatch, image_bytes(size=(300, 150)))

    result = lambda_handler.handler(event(record("m1", ok_body())), None)

    assert result == {"batchItemFailures": []}
    s3 = boto3.client("s3")
    keys = sorted(o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET)["Contents"])
    assert keys == [
        "images/example/manifest.json",
        "images/example/original.jpg",
        "images/example/w128.webp",
        "images/example/w64.webp",
    ]
    head = s3.head_object(Bucket=BUCKET, Key="images/example/w64.webp")
    assert head["ContentType"] == "image/webp"
    assert "immutable" in head["CacheControl"]


def test_redelivered_message_is_not_downloaded_again(
    aws: str, monkeypatch: pytest.MonkeyPatch, image_bytes: ImageFactory
) -> None:
    calls = serve(monkeypatch, image_bytes())
    lambda_handler.handler(event(record("m1", ok_body())), None)
    lambda_handler.handler(event(record("m1", ok_body())), None)
    assert calls == [URL]


def test_transient_failure_is_reported_for_retry(aws: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, TransientFetchError("HTTP 503"))

    result = lambda_handler.handler(event(record("m1", ok_body())), None)

    assert result == {"batchItemFailures": [{"itemIdentifier": "m1"}]}
    assert dlq_messages(aws) == []


def test_permanent_failure_goes_straight_to_dlq(aws: str, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, b"<html>not an image</html>")

    result = lambda_handler.handler(event(record("m1", ok_body())), None)

    assert result == {"batchItemFailures": []}
    [message] = dlq_messages(aws)
    assert json.loads(message["Body"])["image_id"] == "example"
    assert message["MessageAttributes"]["error"]["StringValue"].startswith("UnsupportedImage")
    assert message["MessageAttributes"]["source_message_id"]["StringValue"] == "m1"


def test_without_dlq_permanent_failure_falls_back_to_retry(
    aws: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DLQ_URL")
    clear_caches()

    result = lambda_handler.handler(event(record("m1", "not json")), None)

    assert result == {"batchItemFailures": [{"itemIdentifier": "m1"}]}


def test_mixed_batch_reports_only_the_failed_message(
    aws: str, monkeypatch: pytest.MonkeyPatch, image_bytes: ImageFactory
) -> None:
    good = image_bytes()

    def fetch(url: str, policy: FetchPolicy) -> bytes:
        if "broken" in url:
            raise TransientFetchError("timeout")
        return good

    monkeypatch.setattr("thumbnailer.worker.fetch_image", fetch)
    batch = event(
        record("ok", ok_body(image_id="a")),
        record("retry", ok_body(url=URL + "?broken", image_id="b")),
        record("poison", "{"),
    )

    result = lambda_handler.handler(batch, None)

    assert result == {"batchItemFailures": [{"itemIdentifier": "retry"}]}
    assert len(dlq_messages(aws)) == 1
