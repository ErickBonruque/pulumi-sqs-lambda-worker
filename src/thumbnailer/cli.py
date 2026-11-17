"""Run the worker logic on your machine, without AWS.

    thumbnailer --out out/ https://upload.wikimedia.org/...jpg [more URLs]
    thumbnailer --out out/ --urls-file examples/commons_sample.csv

Each URL becomes ``out/<image_id>/`` with the same files the Lambda writes to S3.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path

from thumbnailer.config import Settings
from thumbnailer.errors import PermanentError, TransientFetchError
from thumbnailer.messages import ThumbnailRequest
from thumbnailer.storage import LocalStore
from thumbnailer.worker import process


def _read_urls(path: Path) -> Iterator[str]:
    """Read URLs from a text file (one per line) or a CSV with a ``url`` column."""
    with path.open(newline="", encoding="utf-8") as handle:
        if path.suffix == ".csv":
            for row in csv.DictReader(handle):
                yield row["url"]
        else:
            yield from (line.strip() for line in handle if line.strip() and line[0] != "#")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="thumbnailer", description=__doc__.split("\n")[0])
    parser.add_argument("urls", nargs="*", help="image URLs (https, allowed hosts only)")
    parser.add_argument("--urls-file", type=Path, help="text file or CSV with a 'url' column")
    parser.add_argument("--out", type=Path, required=True, help="output folder")
    args = parser.parse_args(argv)

    urls = list(args.urls) + (list(_read_urls(args.urls_file)) if args.urls_file else [])
    if not urls:
        parser.error("give at least one URL or --urls-file")

    settings = Settings.from_env({**os.environ, "OUTPUT_BUCKET": "local"})
    store = LocalStore(args.out)
    failed = 0
    for url in urls:
        try:
            result = process(
                ThumbnailRequest.from_body(json.dumps({"source_url": url})),
                store,
                settings.fetch,
                settings.render,
            )
        except (PermanentError, TransientFetchError) as exc:
            failed += 1
            print(f"FAIL {url}: {exc}", file=sys.stderr)
            continue
        status = "skip" if result.skipped else "ok  "
        print(f"{status} {result.image_id} {url}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
