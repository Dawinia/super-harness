"""Lazy loading support for the top-level Click command group."""

from __future__ import annotations

import importlib
from collections.abc import Iterator, Mapping, MutableMapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import click
from click.utils import make_default_short_help

from super_harness.cli.group_options import (
    GroupAwareCommand,
    GroupAwareGroup,
    rewrap_subtree,
)

if TYPE_CHECKING:
    from click.formatting import HelpFormatter


@dataclass(frozen=True)
class CommandSpec:
    target: str
    help: str


class _LazyCommandMapping(MutableMapping[str, click.Command]):
    """Preserve Click's mutable registry while loading specifications on demand."""

    def __init__(self, group: LazyGroup) -> None:
        self._group = group
        self._dynamic_commands: dict[str, click.Command] = {}

    def __getitem__(self, name: str) -> click.Command:
        dynamic = self._dynamic_commands.get(name)
        if dynamic is not None:
            return dynamic
        return self._group._load_spec(name)

    def __setitem__(self, name: str, command: click.Command) -> None:
        self._group._command_order.setdefault(name, None)
        self._group._command_specs.pop(name, None)
        self._dynamic_commands[name] = command

    def __delitem__(self, name: str) -> None:
        if name not in self._group._command_order:
            raise KeyError(name)
        del self._group._command_order[name]
        self._group._command_specs.pop(name, None)
        self._dynamic_commands.pop(name, None)

    def __iter__(self) -> Iterator[str]:
        return iter(self._group._command_order)

    def __len__(self) -> int:
        return len(self._group._command_order)


class LazyGroup(GroupAwareGroup):
    """A Click group backed by an ordered registry of import targets."""

    def __init__(
        self,
        *args: Any,
        command_specs: Mapping[str, CommandSpec],
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        registered_commands = self.commands
        self._command_specs = dict(command_specs)
        self._command_order = dict.fromkeys(self._command_specs)
        self.commands = _LazyCommandMapping(self)
        for name, command in registered_commands.items():
            self.commands[name] = command

    def list_commands(self, ctx: click.Context) -> list[str]:
        return list(self._command_order)

    def get_command(self, ctx: click.Context, name: str) -> click.Command | None:
        try:
            return self.commands[name]
        except KeyError:
            return None

    def _load_spec(self, name: str) -> click.Command:
        spec = self._command_specs.get(name)
        if spec is None:
            raise KeyError(name)
        module_name, attribute = spec.target.split(":", 1)
        command = getattr(importlib.import_module(module_name), attribute)
        if not isinstance(command, click.Command):
            raise TypeError(f"{spec.target!r} did not resolve to a click.Command")
        if isinstance(command, click.Group):
            command.__class__ = GroupAwareGroup
            rewrap_subtree(command)
        else:
            command.__class__ = GroupAwareCommand
        return command

    def format_commands(self, ctx: click.Context, formatter: HelpFormatter) -> None:
        names = self.list_commands(ctx)
        if not names:
            return
        limit = formatter.width - 6 - max(len(name) for name in names)
        rows: list[tuple[str, str]] = []
        for name in names:
            spec = self._command_specs.get(name)
            if spec is not None:
                rows.append((name, make_default_short_help(spec.help, limit)))
                continue
            command = self.commands[name]
            if not command.hidden:
                rows.append((name, command.get_short_help_str(limit)))
        with formatter.section("Commands"):
            formatter.write_dl(rows)
