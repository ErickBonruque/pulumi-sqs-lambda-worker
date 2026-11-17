from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import ImageFactory
from thumbnailer.fetch import FetchPolicy
from thumbnailer.images import RenderOptions
from thumbnailer.messages import ThumbnailRequest
from thumbnailer.storage import LocalStore
from thumbnailer.worker import MANIFEST, process

POLICY = FetchPolicy(allowed_hosts=frozenset({"upload.wikimedia.org"}))
OPTIONS = RenderOptions(widths=(320, 640))
REQUEST = ThumbnailRequest(source_url="https://upload.wikimedia.org/a.png", image_id="img-1")


class RecordingStore(LocalStore):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.writes: list[str] = []

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.writes.append(key)
        super().put(key, data, content_type)


def test_writes_outputs_and_manifest_last(tmp_path: Path, image_bytes: ImageFactory) -> None:
    store = RecordingStore(tmp_path)
    data = image_bytes(size=(1000, 500), fmt="PNG")

    result = process(REQUEST, store, POLICY, OPTIONS, fetch=lambda url, policy: data)

    assert store.writes == [
        "img-1/original.jpg",
        "img-1/w320.webp",
        "img-1/w640.webp",
        "img-1/manifest.json",
    ]
    manifest = json.loads((tmp_path / "img-1" / MANIFEST).read_text())
    assert manifest == result.manifest
    assert manifest["source_format"] == "PNG"
    assert manifest["source_size"] == [1000, 500]
    assert manifest["source_bytes"] == len(data)
    assert [o["width"] for o in manifest["outputs"]] == [1000, 320, 640]
    assert set(manifest["timings_ms"]) == {"download", "render", "upload"}


def test_second_run_is_skipped_without_download(tmp_path: Path, image_bytes: ImageFactory) -> None:
    store = LocalStore(tmp_path)
    data = image_bytes()
    process(REQUEST, store, POLICY, OPTIONS, fetch=lambda url, policy: data)

    def must_not_download(url: str, policy: FetchPolicy) -> bytes:
        raise AssertionError("downloaded again")

    result = process(REQUEST, store, POLICY, OPTIONS, fetch=must_not_download)
    assert result.skipped


def test_failed_render_writes_no_manifest(tmp_path: Path) -> None:
    store = LocalStore(tmp_path)
    with pytest.raises(Exception, match="cannot identify"):
        process(REQUEST, store, POLICY, OPTIONS, fetch=lambda url, policy: b"not an image")
    assert not store.exists("img-1/" + MANIFEST)


def test_local_store_refuses_keys_outside_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="escapes"):
        LocalStore(tmp_path).put("../outside.txt", b"x", "text/plain")
