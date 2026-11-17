from __future__ import annotations

import io

import pytest
from PIL import Image

from tests.conftest import ImageFactory, encode
from thumbnailer.errors import UnsupportedImage
from thumbnailer.images import RenderOptions, decode, fit_width, render, to_rgb

MAX_PIXELS = 10_000_000


def test_decode_returns_image_and_format(image_bytes: ImageFactory) -> None:
    image, fmt = decode(image_bytes(fmt="PNG"), MAX_PIXELS)
    assert fmt == "PNG"
    assert image.size == (800, 400)


def test_decode_rejects_non_images() -> None:
    with pytest.raises(UnsupportedImage):
        decode(b"<html>not an image</html>", MAX_PIXELS)


def test_decode_rejects_formats_outside_the_list(image_bytes: ImageFactory) -> None:
    with pytest.raises(UnsupportedImage, match="ICO"):
        decode(image_bytes(size=(32, 32), fmt="ICO"), MAX_PIXELS)


def test_decode_rejects_huge_canvas_before_decoding(image_bytes: ImageFactory) -> None:
    with pytest.raises(UnsupportedImage, match="pixels"):
        decode(image_bytes(size=(200, 100), fmt="PNG"), max_pixels=10_000)


def test_decode_rejects_truncated_file(image_bytes: ImageFactory) -> None:
    data = image_bytes(fmt="PNG")
    with pytest.raises(UnsupportedImage):
        decode(data[: len(data) // 2], MAX_PIXELS)


def test_decode_takes_first_frame_of_animated_gif() -> None:
    frames = [Image.new("P", (20, 20), color) for color in (1, 2, 3)]
    buffer = io.BytesIO()
    frames[0].save(buffer, format="GIF", save_all=True, append_images=frames[1:])
    image, fmt = decode(buffer.getvalue(), MAX_PIXELS)
    assert fmt == "GIF"
    assert image.tell() == 0


def test_transparent_pixels_become_white() -> None:
    image = Image.new("RGBA", (4, 4), (255, 0, 0, 0))
    assert to_rgb(image).getpixel((0, 0)) == (255, 255, 255)


def test_palette_with_transparency_becomes_white() -> None:
    image = Image.new("P", (4, 4), 0)
    image.info["transparency"] = 0
    rgb = to_rgb(image)
    assert rgb.mode == "RGB"
    assert rgb.getpixel((0, 0)) == (255, 255, 255)


@pytest.mark.parametrize(
    ("mode", "color"), [("L", 128), ("LA", (128, 255)), ("CMYK", (0, 0, 0, 0)), ("1", 1)]
)
def test_other_modes_convert_to_rgb(mode: str, color: object) -> None:
    rgb = to_rgb(Image.new(mode, (4, 4), color))  # type: ignore[arg-type]
    assert rgb.mode == "RGB"


def test_16_bit_greyscale_is_scaled_not_clipped() -> None:
    rgb = to_rgb(Image.new("I;16", (4, 4), 32768))
    assert rgb.getpixel((0, 0)) == (128, 128, 128)


def test_exif_orientation_is_applied() -> None:
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 degrees clockwise when displayed
    data = encode(Image.new("RGB", (40, 20)), "JPEG", exif=exif)
    image, _ = decode(data, MAX_PIXELS)
    assert to_rgb(image).size == (20, 40)


def test_fit_width_keeps_aspect_ratio() -> None:
    assert fit_width(Image.new("RGB", (1000, 500)), 320).size == (320, 160)


def test_fit_width_never_upscales() -> None:
    assert fit_width(Image.new("RGB", (200, 100)), 640).size == (200, 100)


def test_render_outputs(image_bytes: ImageFactory) -> None:
    image, _ = decode(image_bytes(mode="RGBA", size=(1000, 500), fmt="PNG"), MAX_PIXELS)
    outputs = render(image, RenderOptions(widths=(640, 320, 320, 2000)))

    assert [o.name for o in outputs] == ["original.jpg", "w320.webp", "w640.webp", "w2000.webp"]
    assert [(o.width, o.height) for o in outputs] == [
        (1000, 500),
        (320, 160),
        (640, 320),
        (1000, 500),
    ]
    assert outputs[0].content_type == "image/jpeg"
    for output in outputs[1:]:
        assert output.content_type == "image/webp"
        assert Image.open(io.BytesIO(output.data)).format == "WEBP"


def test_render_rejects_non_positive_widths() -> None:
    with pytest.raises(ValueError, match="positive"):
        render(Image.new("RGB", (10, 10)), RenderOptions(widths=(0,)))
