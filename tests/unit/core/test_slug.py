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


def test_slug_rejects_consecutive_dashes() -> None:
    """`a--b` style slugs are illegal — matches npm/Cargo/Go-module conventions.

    Single dashes separating alphanumeric runs only. The old regex
    `^[a-z0-9]([a-z0-9-]*[a-z0-9])?$` was too permissive and allowed runs of
    dashes; the tightened `^[a-z0-9]+(-[a-z0-9]+)*$` forbids them.
    """
    with pytest.raises(SlugError):
        validate_slug("add--foo")


def test_slug_rejects_triple_dash() -> None:
    """Pin the n-dash variant in addition to the 2-dash case for explicit coverage."""
    with pytest.raises(SlugError):
        validate_slug("a---b")
