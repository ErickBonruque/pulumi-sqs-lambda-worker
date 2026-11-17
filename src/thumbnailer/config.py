"""Settings read from environment variables (set by the Pulumi program)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from thumbnailer.fetch import FetchPolicy
from thumbnailer.images import RenderOptions

DEFAULT_USER_AGENT = (
    "pulumi-sqs-lambda-worker/0.1 (+https://github.com/ErickBonruque/pulumi-sqs-lambda-worker)"
)


@dataclass(frozen=True, slots=True)
class Settings:
    bucket: str
    output_prefix: str
    dlq_url: str | None
    fetch: FetchPolicy
    render: RenderOptions

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        bucket = env.get("OUTPUT_BUCKET", "").strip()
        if not bucket:
            raise RuntimeError("OUTPUT_BUCKET is not set")

        prefix = env.get("OUTPUT_PREFIX", "images/").strip().lstrip("/")
        if prefix and not prefix.endswith("/"):
            prefix += "/"

        hosts = frozenset(
            host.strip().lower()
            for host in env.get("ALLOWED_HOSTS", "upload.wikimedia.org").split(",")
            if host.strip()
        )
        if not hosts:
            raise RuntimeError("ALLOWED_HOSTS must list at least one host")

        widths = tuple(
            int(width) for width in env.get("THUMBNAIL_WIDTHS", "320,640,1280").split(",") if width
        )
        return cls(
            bucket=bucket,
            output_prefix=prefix,
            dlq_url=env.get("DLQ_URL") or None,
            fetch=FetchPolicy(
                allowed_hosts=hosts,
                max_bytes=int(env.get("MAX_DOWNLOAD_BYTES", str(20 * 1024 * 1024))),
                timeout_seconds=float(env.get("FETCH_TIMEOUT_SECONDS", "10")),
                user_agent=env.get("USER_AGENT", DEFAULT_USER_AGENT),
            ),
            render=RenderOptions(
                widths=widths,
                max_pixels=int(env.get("MAX_PIXELS", "50000000")),
            ),
        )
