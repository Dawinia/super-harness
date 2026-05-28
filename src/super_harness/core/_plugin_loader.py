"""Dynamic class-loading primitive shared by the yaml-driven registries.

`core/_registry.load_components` (sensors / gates) and
`adapters/registry.load_adapters` both need to take a contributor-supplied
`path` + `class` pair and resolve it to a concrete subclass of an expected
ABC. The actual import-spec dance — `sys.modules` eviction, loading from a
file location, exec'ing the module, attribute lookup, and the
`isinstance + issubclass` safety check — lives here so both call sites stay
in lockstep.

**v0.1 plugin scope:** This executes arbitrary contributor code in the host
process. Sandboxing / permission isolation / per-component resource limits
are deferred to v0.2. The `sys.modules` eviction makes each load re-exec the
file (clean class identity for tests / repeated CLI list calls); reload-on-mtime
is also v0.2.

**Intra-package access:** the leading underscore signals "shared
infrastructure, not a third-party public API". Symbols in `__all__` are stable
across intra-package calls but may break in v0.2.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TypeVar

__all__ = ["load_class_from_path"]

_T = TypeVar("_T")


def load_class_from_path(
    spec_path: Path,
    class_name: str,
    expected_base: type[_T],
    *,
    module_name: str,
    error_label: str,
) -> type[_T]:
    """Load `class_name` from the Python file at `spec_path` and validate its base.

    Args:
        spec_path: Filesystem path to the contributor's module. The caller is
            responsible for verifying the path exists before calling.
        class_name: Name of the class attribute to resolve inside the module.
        expected_base: The ABC the loaded class must subclass. The runtime
            `issubclass` check is the load-bearing safety net (mypy cannot see
            plugin classes). Because the bases are abstract, callers bind them
            once at module scope via `_BASE: type[X] = X  # type: ignore[type-abstract]`.
        module_name: `sys.modules` key + import-spec name. We pop any stale
            entry first so each call re-exec's the file (clean class identity
            for tests + repeated CLI list calls). Use a unique key per logical
            plugin id (e.g. `f"super_harness_user.{id}"`).
        error_label: Prefix for the ImportError / AttributeError / TypeError
            messages (e.g. `f"{yaml_path}: plugin {id!r}"`) so the failure points
            back at the offending yaml entry.

    Returns:
        The resolved class object, narrowed to `type[_T]`.

    Raises:
        ImportError: The import spec could not be built (None / no loader).
        AttributeError: `class_name` is absent from the loaded module.
        TypeError: The resolved object is not a subclass of `expected_base`.
    """
    sys.modules.pop(module_name, None)
    module_spec = importlib.util.spec_from_file_location(module_name, spec_path)
    if module_spec is None or module_spec.loader is None:
        raise ImportError(f"{error_label}: cannot load module spec from {spec_path}")
    mod = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(mod)

    if not hasattr(mod, class_name):
        raise AttributeError(
            f"{error_label} module {spec_path} has no attribute {class_name!r}"
        )
    cls = getattr(mod, class_name)
    if not (isinstance(cls, type) and issubclass(cls, expected_base)):
        raise TypeError(
            f"{error_label} class {class_name!r} in {spec_path} is "
            f"not a {expected_base.__name__} subclass"
        )
    return cls
