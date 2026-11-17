"""Send an SQS-shaped event to the worker running in Docker (see compose.yaml).

    uv run python scripts/local_demo.py --limit 5

Creates the bucket and the DLQ in moto, invokes the function through the
Lambda Runtime Interface Emulator, then lists what landed in S3 and in the DLQ.
One extra message with a host outside the allow-list shows the DLQ path.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
import urllib.request
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import boto3
from botocore.exceptions import HTTPClientError

MOTO = "http://127.0.0.1:5000"
INVOKE = "http://127.0.0.1:9000/2015-03-31/functions/function/invocations"
BUCKET = "thumbnails-local"
DLQ = "thumbnails-dlq"


T = TypeVar("T")


def retry(action: Callable[[], T], seconds: float = 30) -> T:
    """Containers may still be starting: retry connection errors for a while."""
    deadline = time.monotonic() + seconds
    while True:
        try:
            return action()
        except (OSError, HTTPClientError):
            if time.monotonic() > deadline:
                raise
            time.sleep(1)


def sqs_record(body: str) -> dict[str, Any]:
    return {"messageId": str(uuid.uuid4()), "body": body, "eventSource": "aws:sqs"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--urls-file", type=Path, default=Path("examples/commons_sample.csv"))
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()

    # moto accepts any credentials; fixed ones keep real AWS credentials out of the demo.
    session = boto3.Session(
        aws_access_key_id="testing",
        aws_secret_access_key="testing",  # noqa: S106 - moto placeholder, not a secret
        region_name="us-east-1",
    )
    s3 = session.client("s3", endpoint_url=MOTO)
    sqs = session.client("sqs", endpoint_url=MOTO)
    retry(lambda: s3.create_bucket(Bucket=BUCKET))
    dlq_url = sqs.create_queue(QueueName=DLQ)["QueueUrl"]

    with args.urls_file.open(encoding="utf-8") as handle:
        urls = [row["url"] for row in csv.DictReader(handle)][: args.limit]
    records = [sqs_record(json.dumps({"source_url": url})) for url in urls]
    records.append(sqs_record(json.dumps({"source_url": "https://example.com/not-allowed.jpg"})))

    started = time.perf_counter()
    request = urllib.request.Request(
        INVOKE, data=json.dumps({"Records": records}).encode(), method="POST"
    )

    def invoke() -> Any:
        with urllib.request.urlopen(request, timeout=300) as response:  # noqa: S310
            return json.load(response)

    result = retry(invoke)
    print(f"invocation took {time.perf_counter() - started:.1f} s")
    print("response:", json.dumps(result))

    objects = s3.list_objects_v2(Bucket=BUCKET).get("Contents", [])
    print(f"\n{len(objects)} objects in s3://{BUCKET}:")
    for obj in objects[:12]:
        print(f"  {obj['Size']:>9,d}  {obj['Key']}")
    dead = sqs.receive_message(
        QueueUrl=dlq_url, MaxNumberOfMessages=10, MessageAttributeNames=["All"]
    ).get("Messages", [])
    print(f"\n{len(dead)} message(s) in the DLQ:")
    for message in dead:
        print("  ", message["MessageAttributes"]["error"]["StringValue"])


if __name__ == "__main__":
    main()
