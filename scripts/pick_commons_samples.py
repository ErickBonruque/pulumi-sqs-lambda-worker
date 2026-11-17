"""Build examples/commons_sample.csv: public-domain / CC0 images from Wikimedia Commons.

    uv run python scripts/pick_commons_samples.py --per-format 6

Only files whose licence is "Public domain" or "CC0" are kept, so the example
outputs can be shared without attribution problems (the CSV still credits the
author and links the file page). Formats are mixed on purpose to exercise the
colour-mode conversions.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = (
    "pulumi-sqs-lambda-worker/0.1 (+https://github.com/ErickBonruque/pulumi-sqs-lambda-worker)"
)
CATEGORIES = [
    "Featured pictures of astronomy",
    "Featured pictures of maps",
    "Images from NASA",
    "PNG files with alpha channel",
    "Animated GIF files",
    "TIFF files",
    "WebP files",
    "Public domain diagrams",
]
LICENCES = {"Public domain", "CC0"}
MIMES = {"image/jpeg", "image/png", "image/gif", "image/tiff", "image/webp"}
MAX_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 50_000_000
# Licence is not the only filter: no photos of living people, no trademark logos.
EXCLUDE_WORDS = ("logo", "director", "driscoll", "portrait of", "cropped")
FIELDS = ["title", "url", "mime", "bytes", "width", "height", "licence", "author", "page"]


def _get(params: dict[str, str]) -> dict[str, Any]:
    url = API + "?" + urllib.parse.urlencode({**params, "format": "json"})
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - fixed https URL
        result: dict[str, Any] = json.load(response)
        return result


def _plain(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def candidates(category: str, limit: int) -> list[dict[str, Any]]:
    data = _get(
        {
            "action": "query",
            "generator": "categorymembers",
            "gcmtitle": f"Category:{category}",
            "gcmtype": "file",
            "gcmlimit": str(limit),
            "prop": "imageinfo",
            "iiprop": "url|size|mime|extmetadata",
            "iiextmetadatafilter": "LicenseShortName|Artist",
        }
    )
    rows = []
    for page in data.get("query", {}).get("pages", {}).values():
        info = page["imageinfo"][0]
        meta = info.get("extmetadata", {})
        licence = meta.get("LicenseShortName", {}).get("value", "")
        if any(word in page["title"].lower() for word in EXCLUDE_WORDS):
            continue
        if licence not in LICENCES or info["mime"] not in MIMES:
            continue
        if info["size"] > MAX_BYTES or info["width"] * info["height"] > MAX_PIXELS:
            continue
        rows.append(
            {
                "title": page["title"].removeprefix("File:"),
                "url": info["url"].split("?")[0],
                "mime": info["mime"],
                "bytes": info["size"],
                "width": info["width"],
                "height": info["height"],
                "licence": licence,
                "author": _plain(meta.get("Artist", {}).get("value", ""))[:120],
                "page": info["descriptionurl"],
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--per-format", type=int, default=6)
    parser.add_argument("--out", type=Path, default=Path("examples/commons_sample.csv"))
    args = parser.parse_args()

    by_mime: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for category in CATEGORIES:
        for row in candidates(category, limit=200):
            by_mime[row["mime"]].setdefault(row["url"], row)

    chosen = []
    for mime in sorted(by_mime):
        rows = sorted(by_mime[mime].values(), key=lambda r: r["title"])
        chosen.extend(rows[: args.per_format])
        print(f"{mime}: {len(by_mime[mime])} found, {min(len(rows), args.per_format)} kept")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(chosen)
    print(f"wrote {len(chosen)} rows to {args.out}")


if __name__ == "__main__":
    main()
