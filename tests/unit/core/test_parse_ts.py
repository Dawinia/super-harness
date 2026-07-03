"""parse_ts — the single ISO-8601 timestamp primitive. Never raises."""
from datetime import datetime, timedelta, timezone

from super_harness.core.parse_ts import parse_ts


def test_aware_iso_z_form():
    assert parse_ts("2026-07-03T10:00:00Z") == datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc)


def test_aware_iso_offset_form():
    assert parse_ts("2026-07-03T10:00:00+00:00") == datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc)


def test_naive_iso_normalized_to_utc():
    # tz-less string parses NAIVE; primitive attaches UTC so it can't TypeError vs aware entries.
    out = parse_ts("2026-07-03T10:00:00")
    assert out == datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc)
    assert out.tzinfo is not None


def test_datetime_aware_utc_returned_equal():
    dt = datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc)
    out = parse_ts(dt)
    assert out == dt
    assert out.tzinfo == timezone.utc


def test_datetime_non_utc_offset_normalized_to_utc():
    # +05:00 10:00 == 05:00 UTC — the "aware-UTC" contract must hold literally.
    plus5 = timezone(timedelta(hours=5))
    out = parse_ts(datetime(2026, 7, 3, 10, 0, tzinfo=plus5))
    assert out == datetime(2026, 7, 3, 5, 0, tzinfo=timezone.utc)
    assert out.tzinfo == timezone.utc


def test_string_non_utc_offset_normalized_to_utc():
    out = parse_ts("2026-07-03T10:00:00+05:00")
    assert out == datetime(2026, 7, 3, 5, 0, tzinfo=timezone.utc)
    assert out.tzinfo == timezone.utc


def test_datetime_naive_gets_utc_attached():
    out = parse_ts(datetime(2026, 7, 3, 10, 0))
    assert out == datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc)
    assert out.tzinfo == timezone.utc


def test_empty_string_is_none():
    assert parse_ts("") is None


def test_malformed_string_is_none():
    assert parse_ts("not-a-timestamp") is None


def test_none_is_none():
    assert parse_ts(None) is None


def test_wrong_type_is_none():
    assert parse_ts(12345) is None
    assert parse_ts([]) is None


def test_naive_and_aware_are_mutually_comparable():
    # The whole point: a mixed pair must not TypeError under comparison.
    a = parse_ts("2026-07-03T10:00:00")        # naive source
    b = parse_ts("2026-07-03T10:00:01+00:00")  # aware source
    assert a is not None and b is not None
    assert a < b  # would raise TypeError if either stayed naive


def test_pathological_tzinfo_returns_none_not_raise():
    """The 'never raises' contract must hold even for a datetime whose tzinfo
    raises from utcoffset() (astimezone would otherwise propagate it). Feeds the
    gate hot path — a raise here would exit the hook 1 = silent fail-open."""
    from datetime import tzinfo

    class _BadTZ(tzinfo):
        def utcoffset(self, dt):
            raise ValueError("boom")

        def tzname(self, dt):
            return "BAD"

        def dst(self, dt):
            return None

    bad = datetime(2026, 1, 1, tzinfo=_BadTZ())
    assert parse_ts(bad) is None  # must not raise
