from __future__ import annotations

import io
from collections.abc import Callable
from typing import Any

import pytest
from PIL import Image

ImageFactory = Callable[..., bytes]


def encode(image: Image.Image, fmt: str, **params: Any) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **params)
    return buffer.getvalue()


@pytest.fixture
def image_bytes() -> ImageFactory:
    """Build a synthetic image file: ``image_bytes(mode="RGBA", size=(80, 40), fmt="PNG")``."""

    def build(
        mode: str = "RGB",
        size: tuple[int, int] = (800, 400),
        fmt: str = "JPEG",
        color: Any = None,
        **params: Any,
    ) -> bytes:
        if color is None:
            color = {"RGB": (200, 30, 30), "RGBA": (200, 30, 30, 255), "L": 128}.get(mode, 0)
        return encode(Image.new(mode, size, color), fmt, **params)

    return build
