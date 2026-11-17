"""Where outputs are written: S3 in production, a folder for local runs."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from botocore.exceptions import ClientError

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client


class Store(Protocol):
    def exists(self, key: str) -> bool: ...

    def put(self, key: str, data: bytes, content_type: str) -> None: ...


class S3Store:
    """Write objects under ``prefix`` in one bucket.

    ``exists`` uses HeadObject. Note for IAM: without ``s3:ListBucket`` S3
    answers 403 (not 404) for a missing key, so the role needs ListBucket.
    """

    def __init__(self, client: S3Client, bucket: str, prefix: str = "") -> None:
        self._client = client
        self.bucket = bucket
        self.prefix = prefix

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self.bucket, Key=self.prefix + key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise
        return True

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self._client.put_object(
            Bucket=self.bucket,
            Key=self.prefix + key,
            Body=data,
            ContentType=content_type,
            CacheControl="public, max-age=31536000, immutable",
        )


class LocalStore:
    """Write objects as files under ``root`` (for local runs and benchmarks)."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise ValueError(f"key {key!r} escapes the output folder")
        return path

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
