import json

import pytest

from thumbnailer.errors import InvalidMessage
from thumbnailer.messages import ThumbnailRequest

URL = "https://upload.wikimedia.org/wikipedia/commons/a/a9/Example.jpg"


def test_image_id_defaults_to_a_stable_hash_of_the_url() -> None:
    first = ThumbnailRequest.from_body(json.dumps({"source_url": URL}))
    second = ThumbnailRequest.from_body(json.dumps({"source_url": URL}))
    assert first.image_id == second.image_id
    assert len(first.image_id) == 20


def test_explicit_image_id_is_kept() -> None:
    request = ThumbnailRequest.from_body(json.dumps({"source_url": URL, "image_id": "cat-01.v2"}))
    assert request.image_id == "cat-01.v2"


def test_round_trip() -> None:
    request = ThumbnailRequest(source_url=URL, image_id="abc")
    assert ThumbnailRequest.from_body(request.to_body()) == request


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        "[1, 2]",
        "{}",
        json.dumps({"source_url": ""}),
        json.dumps({"source_url": 42}),
        json.dumps({"source_url": "https://x/" + "a" * 2048}),
    ],
)
def test_rejects_bad_bodies(body: str) -> None:
    with pytest.raises(InvalidMessage):
        ThumbnailRequest.from_body(body)


@pytest.mark.parametrize("image_id", ["../etc", "a/b", "", ".hidden", "a..b", "x" * 129, 7])
def test_rejects_unsafe_image_ids(image_id: object) -> None:
    with pytest.raises(InvalidMessage):
        ThumbnailRequest.from_body(json.dumps({"source_url": URL, "image_id": image_id}))
