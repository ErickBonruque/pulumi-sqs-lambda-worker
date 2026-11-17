"""Decode an image, normalise it to RGB and render JPEG + WebP outputs."""

from __future__ import annotations

import io
from collections.abc import Sequence
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

from thumbnailer.errors import UnsupportedImage

ACCEPTED_FORMATS = frozenset({"JPEG", "PNG", "WEBP", "GIF", "TIFF", "BMP"})
_BACKGROUND = (255, 255, 255)


@dataclass(frozen=True, slots=True)
class Rendition:
    """One encoded output file."""

    name: str
    content_type: str
    width: int
    height: int
    data: bytes


@dataclass(frozen=True, slots=True)
class RenderOptions:
    widths: tuple[int, ...] = (320, 640, 1280)
    jpeg_quality: int = 85
    webp_quality: int = 80
    max_pixels: int = 50_000_000


def decode(data: bytes, max_pixels: int) -> tuple[Image.Image, str]:
    """Open ``data`` as an image and return it with its source format.

    The size check runs before the pixels are decoded, so a small file that
    declares a huge canvas (a "decompression bomb") is rejected cheaply.
    """
    try:
        image = Image.open(io.BytesIO(data))
    except (UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise UnsupportedImage(f"cannot identify image: {exc}") from exc

    source_format = image.format or "unknown"
    if source_format not in ACCEPTED_FORMATS:
        raise UnsupportedImage(f"format {source_format} is not accepted")
    width, height = image.size
    if width * height > max_pixels:
        raise UnsupportedImage(f"{width}x{height} is more than {max_pixels} pixels")

    try:
        image.seek(0)  # first frame of an animated GIF/WebP
        image.load()
    except (OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise UnsupportedImage(f"cannot decode image: {exc}") from exc
    return image, source_format


def to_rgb(image: Image.Image) -> Image.Image:
    """Apply EXIF rotation and convert any mode to RGB.

    Transparent pixels are painted on a white background (JPEG has no alpha).
    """
    image = ImageOps.exif_transpose(image)
    if image.mode == "RGB":
        return image
    has_alpha = image.mode in {"RGBA", "LA", "PA"} or (
        image.mode == "P" and "transparency" in image.info
    )
    if has_alpha:
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, _BACKGROUND)
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    if image.mode in {"I;16", "I;16B", "I;16L", "I"}:
        # 16/32-bit greyscale: scale to 8 bits before the RGB conversion.
        image = image.convert("I").point(lambda value: value / 256).convert("L")
    return image.convert("RGB")


def fit_width(image: Image.Image, width: int) -> Image.Image:
    """Resize to ``width`` keeping the aspect ratio. Never makes an image larger."""
    if width >= image.width:
        return image.copy()
    height = max(1, round(image.height * width / image.width))
    return image.resize((width, height), Image.Resampling.LANCZOS, reducing_gap=3.0)


def _encode(image: Image.Image, fmt: str, **params: int | bool) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **params)
    return buffer.getvalue()


def render(image: Image.Image, options: RenderOptions) -> list[Rendition]:
    """Return the normalised JPEG plus one WebP thumbnail per width."""
    rgb = to_rgb(image)
    outputs = [
        Rendition(
            name="original.jpg",
            content_type="image/jpeg",
            width=rgb.width,
            height=rgb.height,
            data=_encode(rgb, "JPEG", quality=options.jpeg_quality, optimize=True),
        )
    ]
    for width in _unique_sorted(options.widths):
        thumb = fit_width(rgb, width)
        outputs.append(
            Rendition(
                name=f"w{width}.webp",
                content_type="image/webp",
                width=thumb.width,
                height=thumb.height,
                data=_encode(thumb, "WEBP", quality=options.webp_quality, method=4),
            )
        )
    return outputs


def _unique_sorted(widths: Sequence[int]) -> list[int]:
    if any(width <= 0 for width in widths):
        raise ValueError("thumbnail widths must be positive")
    return sorted(set(widths))
