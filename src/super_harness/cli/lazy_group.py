"""Lazy loading support for the top-level Click command group."""

from __future__ import annotations

import importlib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

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


class _LazyCommandMapping(Mapping[str, click.Command]):
    """Expose lazy commands to existing command-tree introspection code."""

    def __init__(self, group: LazyGroup) -> None:
        self._group = group

    def __getitem__(self, name: str) -> click.Command:
        command = self._group.get_command(click.Context(self._group), name)
        if command is None:
            raise KeyError(name)
        return command

    def __iter__(self) -> Iterator[str]:
        return iter(self._group._command_specs)

    def __len__(self) -> int:
        return len(self._group._command_specs)


class LazyGroup(GroupAwareGroup):
    """A Click group backed by an ordered registry of import targets."""

    def __init__(
        self,
        *args: object,
        command_specs: Mapping[str, CommandSpec],
        **kwargs: object,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._command_specs = dict(command_specs)
        self.commands = _LazyCommandMapping(self)  # type: ignore[assignment]

    def list_commands(self, ctx: click.Context) -> list[str]:
        return list(self._command_specs)

    def get_command(self, ctx: click.Context, name: str) -> click.Command | None:
        spec = self._command_specs.get(name)
        if spec is None:
            return None
        module_name, attribute = spec.target.split(":", 1)
        command = getattr(importlib.import_module(module_name), attribute)
        if isinstance(command, click.Group):
            command.__class__ = GroupAwareGroup
            rewrap_subtree(command)
        else:
            command.__class__ = GroupAwareCommand
        return command

    def format_commands(self, ctx: click.Context, formatter: HelpFormatter) -> None:
        if not self._command_specs:
            return
        limit = formatter.width - 6 - max(len(name) for name in self._command_specs)
        rows = [
            (name, make_default_short_help(spec.help, limit))
            for name, spec in self._command_specs.items()
        ]
        with formatter.section("Commands"):
            formatter.write_dl(rows)
