"""Process one request: download, render, upload, write the manifest."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from thumbnailer.fetch import FetchPolicy, fetch_image
from thumbnailer.images import RenderOptions, decode, render
from thumbnailer.messages import ThumbnailRequest
from thumbnailer.storage import Store

MANIFEST = "manifest.json"

Fetcher = Callable[[str, FetchPolicy], bytes]


@dataclass(frozen=True, slots=True)
class Result:
    image_id: str
    skipped: bool
    manifest: dict[str, Any] | None = None


def process(
    request: ThumbnailRequest,
    store: Store,
    fetch_policy: FetchPolicy,
    render_options: RenderOptions,
    fetch: Fetcher | None = None,
) -> Result:
    """Create all outputs for ``request``.

    Idempotent: the manifest is written last, so its presence means every
    output exists. A retry after a crash in the middle redoes the whole image,
    which is safe because each object is overwritten with the same content.
    """
    folder = f"{request.image_id}/"
    if store.exists(folder + MANIFEST):
        return Result(image_id=request.image_id, skipped=True)

    t0 = time.perf_counter()
    data = (fetch or fetch_image)(request.source_url, fetch_policy)
    t1 = time.perf_counter()
    image, source_format = decode(data, render_options.max_pixels)
    renditions = render(image, render_options)
    t2 = time.perf_counter()
    for item in renditions:
        store.put(folder + item.name, item.data, item.content_type)
    t3 = time.perf_counter()

    manifest: dict[str, Any] = {
        "image_id": request.image_id,
        "source_url": request.source_url,
        "source_format": source_format,
        "source_bytes": len(data),
        "source_size": [image.width, image.height],
        "outputs": [
            {
                "key": folder + item.name,
                "content_type": item.content_type,
                "width": item.width,
                "height": item.height,
                "bytes": len(item.data),
            }
            for item in renditions
        ],
        "timings_ms": {
            "download": _ms(t1 - t0),
            "render": _ms(t2 - t1),
            "upload": _ms(t3 - t2),
        },
    }
    body = json.dumps(manifest, indent=2).encode()
    store.put(folder + MANIFEST, body, "application/json")
    return Result(image_id=request.image_id, skipped=False, manifest=manifest)


def _ms(seconds: float) -> float:
    return round(seconds * 1000, 1)
