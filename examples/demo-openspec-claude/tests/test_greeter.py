"""Placeholder test for the demo greeter module. Implement after greeter.py."""

import pytest
from src.greeter import greet


def test_greet_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        greet("world")
