"""The single ISO-8601 timestamp parsing primitive for the core layer.

Converges two prior copies (`active_change._parse_ts` and the `reducer` inline
drift parse) into one never-raising function. Returns `datetime | None` so each
caller supplies its own policy for the unparseable case:

- ORDERING (active_change): wrap as ``parse_ts(v) or _TS_MIN`` so a bad value
  sorts lowest and never wins.
- DRIFT DETECTION (reducer): treat ``None`` as "skip the comparison" — an
  unparseable timestamp must NOT trigger a spurious drift warning.

Returning a sentinel here instead of ``None`` would be wrong for the reducer
(a sentinel would compare as an enormous backward jump). Keep it tri-valued.

All returned datetimes are aware-UTC (aware inputs are CONVERTED via
``astimezone``, not just accepted as-is) so a mixed naive/aware pair (e.g. a
``Z`` form vs a tz-less form across two events) can be compared without
``TypeError`` — the crash the reducer's old ``except ValueError``-only copy
did not catch.
"""
from __future__ import annotations

from datetime import datetime, timezone


def _to_utc(dt: datetime) -> datetime | None:
    """Normalize a ``datetime`` to aware UTC, or ``None`` if that is impossible.
    A naive value gets UTC attached (cannot raise). An aware value is converted
    via ``astimezone`` — which can raise for a pathological ``tzinfo`` whose
    ``utcoffset`` raises, or ``OverflowError`` at the ``datetime.min``/``max``
    boundary. Both collapse to ``None`` so ``parse_ts`` keeps its never-raise
    contract on the gate hot path."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    try:
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def parse_ts(value: object) -> datetime | None:
    """Parse a timestamp into an aware-UTC ``datetime``, or ``None`` if it is
    absent/malformed/wrong-type/unnormalizable. NEVER raises (this feeds the gate
    hot path via ``active_change``).

    Accepts the shapes a state.yaml / events.jsonl value can take:
    - ``datetime`` → converted to aware UTC (naive gets UTC attached; a non-UTC
      offset is converted to the same instant in UTC).
    - ISO ``str`` with ``Z`` or ``+00:00`` (or any offset, or tz-less) → parsed,
      then converted to aware UTC as above.
    - empty / malformed / ``None`` / any other type / unnormalizable → ``None``.
    """
    if isinstance(value, datetime):
        return _to_utc(value)
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _to_utc(dt)
