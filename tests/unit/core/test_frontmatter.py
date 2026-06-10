from super_harness.core.frontmatter import split_frontmatter


def test_splits_mapping_and_body():
    assert split_frontmatter("---\na: 1\n---\nbody\n") == ({"a": 1}, "body")


def test_none_on_no_fence():
    assert split_frontmatter("no fence\n") is None


def test_none_on_unclosed():
    assert split_frontmatter("---\na: 1\n") is None


def test_none_on_non_mapping():
    assert split_frontmatter("---\n- a\n- b\n---\nx\n") is None


def test_none_on_bad_yaml():
    assert split_frontmatter("---\nkey: [unclosed\n---\nx\n") is None
