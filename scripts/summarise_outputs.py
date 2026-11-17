"""Summarise the manifests of a run: sizes before and after, render time.

    uv run python scripts/summarise_outputs.py out/commons --csv results/commons_run.csv

Prints a Markdown table per source format and writes one CSV row per image.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from statistics import median
from typing import Any


def load(root: Path) -> list[dict[str, Any]]:
    rows = []
    for path in sorted(root.glob("*/manifest.json")):
        manifest = json.loads(path.read_text())
        outputs = {o["key"].rsplit("/", 1)[1]: o for o in manifest["outputs"]}
        row: dict[str, Any] = {
            "image_id": manifest["image_id"],
            "format": manifest["source_format"],
            "width": manifest["source_size"][0],
            "height": manifest["source_size"][1],
            "source_bytes": manifest["source_bytes"],
            "jpeg_bytes": outputs["original.jpg"]["bytes"],
            "render_ms": manifest["timings_ms"]["render"],
            "download_ms": manifest["timings_ms"]["download"],
        }
        for name, output in outputs.items():
            if name.endswith(".webp"):
                row[f"{name.removesuffix('.webp')}_bytes"] = output["bytes"]
        rows.append(row)
    return rows


def markdown(rows: list[dict[str, Any]]) -> str:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row["format"]].append(row)
    groups["all"] = rows

    lines = [
        "| Source format | Images | Median source | Median w640.webp | w640 / source | "
        "Median render (local) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, group in sorted(groups.items(), key=lambda item: (item[0] == "all", item[0])):
        source = median(r["source_bytes"] for r in group)
        thumb = median(r["w640_bytes"] for r in group)
        ratio = median(r["w640_bytes"] / r["source_bytes"] for r in group)
        render_ms = median(r["render_ms"] for r in group)
        label = f"**{name}**" if name == "all" else name
        lines.append(
            f"| {label} | {len(group)} | {source / 1e6:.2f} MB | {thumb / 1e3:.0f} KB | "
            f"{ratio:.1%} | {render_ms / 1000:.2f} s |"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("root", type=Path, help="output folder of a run")
    parser.add_argument("--csv", type=Path, help="write one row per image here")
    args = parser.parse_args()

    rows = load(args.root)
    if not rows:
        raise SystemExit(f"no manifest.json under {args.root}")
    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        fields = sorted({key for row in rows for key in row}, key=list(rows[0]).index)
        with args.csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    total_in = sum(r["source_bytes"] for r in rows)
    total_out = sum(r["w640_bytes"] for r in rows)
    print(markdown(rows))
    print(f"\nTotal: {total_in / 1e6:.1f} MB of sources -> {total_out / 1e6:.2f} MB of w640 files")


if __name__ == "__main__":
    main()
