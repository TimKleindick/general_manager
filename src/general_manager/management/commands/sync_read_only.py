"""Explicit deployment maintenance for registered read-only managers."""

from __future__ import annotations

from argparse import ArgumentParser
from collections.abc import Iterable, Sequence
from typing import cast

from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from general_manager.interface.capabilities.read_only.management import (
    ReadOnlyManagementCapability,
    _ReadOnlyInterface,
    _SchemaConnection,
    _suppress_automatic_sync,
)
from general_manager.interface.infrastructure.startup_hooks import (
    order_interfaces_by_dependency,
)
from general_manager.manager.meta import GeneralManagerMeta


def _manager_path(interface: _ReadOnlyInterface) -> str:
    """Identify a bound manager without importing arbitrary selector input."""
    manager = interface._parent_class
    return f"{manager.__module__}.{manager.__qualname__}"


def _capability(interface: _ReadOnlyInterface) -> ReadOnlyManagementCapability:
    """Use the manager's configured management capability, including overrides."""
    return cast(
        ReadOnlyManagementCapability,
        interface.require_capability(
            "read_only_management", expected_type=ReadOnlyManagementCapability
        ),
    )


def _select_interfaces(value: object) -> list[_ReadOnlyInterface]:
    """Validate selectors and include transitive read-only relation dependencies."""
    interfaces = list(
        dict.fromkeys(
            cast(_ReadOnlyInterface, manager.Interface)
            for manager in GeneralManagerMeta.all_classes
            if getattr(manager.Interface, "_interface_type", None) == "readonly"
        )
    )
    if value is None:
        selected = interfaces
    else:
        selectors: tuple[object, ...]
        if isinstance(value, str):
            selectors = (value,)
        elif isinstance(value, Iterable):
            selectors = tuple(value)
        else:
            selectors = ()
        if not selectors or any(
            not isinstance(selector, str) or not selector for selector in selectors
        ):
            message = "--manager must contain one or more nonempty manager names."
            raise CommandError(message)
        selected = []
        for selector in selectors:
            matches = [
                interface
                for interface in interfaces
                if selector
                in (interface._parent_class.__name__, _manager_path(interface))
            ]
            if not matches:
                message = f"Unknown read-only manager: {selector}."
                raise CommandError(message)
            if len(matches) > 1:
                paths = ", ".join(sorted(_manager_path(item) for item in matches))
                message = (
                    f"Ambiguous read-only manager {selector}; use one of: {paths}."
                )
                raise CommandError(message)
            if matches[0] not in selected:
                selected.append(matches[0])

    dependencies: dict[_ReadOnlyInterface, set[_ReadOnlyInterface]] = {}
    pending = list(selected)
    while pending:
        interface = pending.pop(0)
        if interface in dependencies:
            continue
        dependencies[interface] = (
            ReadOnlyManagementCapability._related_readonly_interfaces(interface)
        )
        pending.extend(sorted(dependencies[interface], key=_manager_path))

    interface_list = sorted(dependencies, key=_manager_path)
    ordered = cast(
        list[_ReadOnlyInterface],
        order_interfaces_by_dependency(
            interface_list,
            lambda interface: set(dependencies[cast(_ReadOnlyInterface, interface)]),
        ),
    )
    positions = {interface: index for index, interface in enumerate(ordered)}
    if any(
        positions[dependency] >= positions[interface]
        for interface in ordered
        for dependency in dependencies[interface]
    ):
        message = "Read-only dependency cycle; cannot determine a safe sync order."
        raise CommandError(message)
    return ordered


class Command(BaseCommand):
    help = "Synchronize read-only managers and their dependencies after migrations."

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Accept repeatable unique class names or module-qualified manager paths."""
        parser.add_argument(
            "--manager",
            action="append",
            dest="managers",
            help="Read-only manager name or module-qualified class path. Repeatable; defaults to all.",
        )

    def run_from_argv(self, argv: Sequence[str]) -> None:
        """Avoid duplicate automatic read-only sync before this explicit CLI step."""
        with _suppress_automatic_sync():
            super().run_from_argv(argv)

    def handle(self, *_args: object, **options: object) -> None:
        """Preflight all schemas, then sync in dependency order or fail loudly.

        No global transaction is added: completed managers may remain synced if
        a later manager fails. Re-running the command converges the payloads.
        """
        interfaces = _select_interfaces(options.get("managers"))
        # The public sync API logs schema warnings and returns without writing.
        # Deployment maintenance must instead fail before claiming success.
        for interface in interfaces:
            try:
                capability = _capability(interface)
                warnings = capability.ensure_schema_is_up_to_date(
                    interface,
                    interface._parent_class,
                    interface._model,
                    connection=cast(
                        _SchemaConnection,
                        connections[capability._database_alias(interface)],
                    ),
                )
            except Exception as exc:
                message = f"Read-only schema check failed for {_manager_path(interface)}: {exc}"
                raise CommandError(message) from exc
            if warnings:
                detail = "; ".join(str(warning) for warning in warnings)
                message = f"Read-only schema check failed for {_manager_path(interface)}: {detail}"
                raise CommandError(message)

        for interface in interfaces:
            try:
                _capability(interface).sync_data(interface, schema_validated=True)
            except Exception as exc:
                message = f"Read-only sync failed for {_manager_path(interface)}: {exc}"
                raise CommandError(message) from exc
            self.stdout.write(f"Synchronized {_manager_path(interface)}.")
        self.stdout.write(
            self.style.SUCCESS(f"Synchronized {len(interfaces)} read-only managers.")
        )
