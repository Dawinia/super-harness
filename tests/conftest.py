# tests-as-package layout: tests/__init__.py + tests/unit/__init__.py make
# pytest collection rootdir-stable AND allow `from tests.unit.fixtures import X`
# in later phases (e.g., shared verification-runner fixtures). Keep this if
# you add `tests/utils.py`; remove `__init__.py` files if you prefer
# rootdir-only collection.
