import pytest

from thumbnailer.config import Settings


def test_defaults() -> None:
    settings = Settings.from_env({"OUTPUT_BUCKET": "b"})
    assert settings.output_prefix == "images/"
    assert settings.fetch.allowed_hosts == frozenset({"upload.wikimedia.org"})
    assert settings.render.widths == (320, 640, 1280)
    assert settings.dlq_url is None
    assert "github.com" in settings.fetch.user_agent


def test_values_are_normalised() -> None:
    settings = Settings.from_env(
        {
            "OUTPUT_BUCKET": "b",
            "OUTPUT_PREFIX": "/thumbs",
            "ALLOWED_HOSTS": " Example.org, images.example.org ,",
            "THUMBNAIL_WIDTHS": "100,200,",
        }
    )
    assert settings.output_prefix == "thumbs/"
    assert settings.fetch.allowed_hosts == frozenset({"example.org", "images.example.org"})
    assert settings.render.widths == (100, 200)


def test_empty_prefix_writes_at_bucket_root() -> None:
    assert Settings.from_env({"OUTPUT_BUCKET": "b", "OUTPUT_PREFIX": ""}).output_prefix == ""


@pytest.mark.parametrize(
    "env", [{}, {"OUTPUT_BUCKET": " "}, {"OUTPUT_BUCKET": "b", "ALLOWED_HOSTS": " , "}]
)
def test_missing_required_values(env: dict[str, str]) -> None:
    with pytest.raises(RuntimeError):
        Settings.from_env(env)
