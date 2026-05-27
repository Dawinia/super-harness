import pytest

from super_harness.core.slug import SlugError, validate_slug


@pytest.mark.parametrize(
    "slug",
    [
        "add-foo",
        "2026-05-27-add-foo",
        "feat-mobile-auth",
        "a1b",
        "x" * 80,
    ],
)
def test_valid(slug: str) -> None:
    validate_slug(slug)


@pytest.mark.parametrize(
    "slug",
    [
        "",
        "ab",
        "-leading-dash",
        "trailing-dash-",
        "X-uppercase",
        "has_underscore",
        "has space",
        "中文",
        "x" * 81,
        "punct!",
    ],
)
def test_invalid(slug: str) -> None:
    with pytest.raises(SlugError):
        validate_slug(slug)
