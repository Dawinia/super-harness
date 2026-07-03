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


def _to_utc(dt: datetime) -> datetime:
    """Normalize a ``datetime`` to aware UTC. A naive value gets UTC attached;
    an aware value is converted to the same instant in UTC. May raise for exotic
    inputs (a ``tzinfo`` whose ``utcoffset`` raises, a min/max boundary
    ``OverflowError``, or a ``datetime`` subclass overriding ``replace``) — the
    outer ``parse_ts`` guard turns any such raise into ``None``."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_ts(value: object) -> datetime | None:
    """Parse a timestamp into an aware-UTC ``datetime``, or ``None`` if it is
    absent/malformed/wrong-type/unnormalizable. NEVER raises for ANY input (this
    feeds the gate hot path via ``active_change`` — a raise there exits the hook
    1, which Claude Code treats as non-blocking = silent fail-open).

    Accepts the shapes a state.yaml / events.jsonl value can take:
    - ``datetime`` → converted to aware UTC (naive gets UTC attached; a non-UTC
      offset is converted to the same instant in UTC).
    - ISO ``str`` with ``Z`` or ``+00:00`` (or any offset, or tz-less) → parsed,
      then converted to aware UTC as above.
    - empty / malformed / ``None`` / any other type / unnormalizable → ``None``.

    A single outer ``except Exception`` is the belt: real callers only ever pass
    stdlib ``str``/``datetime`` (PyYAML / json / the events reader), but a hostile
    subclass overriding ``replace``/``fromisoformat`` semantics must still resolve
    to ``None``, never propagate.
    """
    try:
        if isinstance(value, datetime):
            return _to_utc(value)
        if not isinstance(value, str) or not value:
            return None
        return _to_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except Exception:
        return None
