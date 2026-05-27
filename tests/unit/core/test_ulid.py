from super_harness.core.ulid import new_event_id


def test_ulid_unique():
    ids = {new_event_id() for _ in range(1000)}
    assert len(ids) == 1000


def test_ulid_prefix_and_length():
    eid = new_event_id()
    assert eid.startswith("ev_")
    # "ev_" (3 chars) + canonical ULID (26 chars) = 29 chars total
    assert len(eid) == 29


def test_ulid_alphabet():
    """ULID's Crockford base32 alphabet excludes I, L, O, U."""
    eid = new_event_id()
    body = eid[3:]  # strip "ev_" prefix
    assert all(c in "0123456789ABCDEFGHJKMNPQRSTVWXYZ" for c in body)
