"""Seed selected GeneralManager classes through their factories."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Literal

from django.core.management.base import BaseCommand, CommandError, CommandParser

from general_manager.manager.meta import GeneralManagerMeta
from general_manager.seeding.manager_landscape import (
    InvalidSeedTargetError,
    ManagerSeedFailure,
    ManagerSelectionError,
    SeedableManagerCollisionError,
    build_seed_plan,
    discover_seedable_managers,
    execute_seed_plan,
    parse_target_overrides,
    select_seed_targets,
)


FAILURE_COMMAND_ERROR = "Seeding completed with failures"
type _OutputFormat = Literal["human", "json"]


def _format_failure_summary(summary: str) -> str:
    return f"{FAILURE_COMMAND_ERROR}: {summary}"


def _string_list_option_error(flag_name: str) -> str:
    return f"{flag_name} must be provided as string values."


def _bool_option_error(flag_name: str) -> str:
    return f"{flag_name} must be true or false."


def _int_option_error(flag_name: str) -> str:
    return f"{flag_name} must be an integer."


def _string_list_option(
    options: Mapping[str, object],
    name: str,
    flag_name: str,
) -> list[str]:
    raw = options.get(name, ())
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, Sequence):
        values = list(raw)
        if all(isinstance(value, str) for value in values):
            return values
    raise CommandError(_string_list_option_error(flag_name))


def _bool_option(options: Mapping[str, object], name: str, flag_name: str) -> bool:
    raw = options.get(name, False)
    if isinstance(raw, bool):
        return raw
    raise CommandError(_bool_option_error(flag_name))


def _int_option(options: Mapping[str, object], name: str, flag_name: str) -> int:
    raw = options.get(name)
    if isinstance(raw, bool):
        raise CommandError(_int_option_error(flag_name))
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        try:
            return int(raw)
        except ValueError as exc:
            raise CommandError(_int_option_error(flag_name)) from exc
    raise CommandError(_int_option_error(flag_name))


def _output_format_option(options: Mapping[str, object]) -> _OutputFormat:
    raw = options.get("output_format", "human")
    if raw == "human":
        return "human"
    if raw == "json":
        return "json"
    message = "--output-format must be 'human' or 'json'."
    raise CommandError(message)


class Command(BaseCommand):
    help = "Seed selected GeneralManager classes using their factories."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--manager",
            action="append",
            dest="managers",
            default=[],
            help="Manager class name to seed. Repeatable.",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            dest="include_all",
            help="Seed every discovered manager that exposes Factory.create_batch.",
        )
        parser.add_argument(
            "--count",
            type=int,
            default=1,
            help="Default desired minimum row count per selected manager.",
        )
        parser.add_argument(
            "--target",
            action="append",
            dest="targets",
            default=[],
            help="Per-manager target in NAME=COUNT format. Repeatable.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=100,
            help="Maximum number of rows to create per transaction.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            dest="dry_run",
            help="Print the seed plan without creating data.",
        )
        parser.add_argument(
            "--output-format",
            choices=("human", "json"),
            default="human",
            dest="output_format",
            help="Dry-run output format.",
        )
        parser.add_argument(
            "--continue-on-error",
            action="store_true",
            dest="continue_on_error",
            help="Continue with later managers after a manager fails.",
        )

    def handle(self, *_args: object, **options: object) -> None:
        """Run manager seeding with explicit selection and predictable exits.

        A run must pass at least one ``--manager`` or ``--all``. ``--all`` and
        ``--manager`` are mutually exclusive; seeding stops at the first failure
        unless ``--continue-on-error`` is set. Command-line input is validated
        by Django's parser; programmatic ``call_command`` values are normalized
        where safe and otherwise rejected with ``CommandError``. Successful runs
        exit normally, while selection, discovery, target, output-format, and
        seeding errors are exposed to command callers as ``CommandError``.

        Args:
            *_args: Positional command arguments supplied by Django. This
                command does not define positional arguments.
            **options: Parsed Django management options. ``manager`` and
                ``target`` are the public ``call_command()`` keyword names;
                Django stores them internally as ``managers`` and ``targets``.
                These values may be ``None``, strings, or string sequences when
                supplied programmatically; ``None`` is treated as omitted.
                ``count`` and ``batch_size`` may be integers or strings accepted
                by ``int()``, but booleans are rejected; non-positive values
                fail normal selection validation. Boolean switches must be
                booleans.

        Raises:
            CommandError: If option types are invalid, selection/target
                validation fails, dry-run output format is unsupported, or
                seeding reports a failure.
        """

        selected_managers = _string_list_option(options, "managers", "--manager")
        target_overrides = _string_list_option(options, "targets", "--target")
        include_all = _bool_option(options, "include_all", "--all")
        dry_run = _bool_option(options, "dry_run", "--dry-run")
        continue_on_error = _bool_option(
            options,
            "continue_on_error",
            "--continue-on-error",
        )
        default_count = _int_option(options, "count", "--count")
        batch_size = _int_option(options, "batch_size", "--batch-size")
        output_format = _output_format_option(options)

        try:
            overrides = parse_target_overrides(target_overrides)
            if include_all and selected_managers:
                raise ManagerSelectionError.conflicting_selection()
            managers_by_name = discover_seedable_managers(
                GeneralManagerMeta.all_classes
            )
            targets = select_seed_targets(
                managers_by_name=managers_by_name,
                selected_names=selected_managers,
                include_all=include_all,
                default_count=default_count,
                overrides=overrides,
            )
        except (
            InvalidSeedTargetError,
            ManagerSelectionError,
            SeedableManagerCollisionError,
        ) as exc:
            raise CommandError(str(exc)) from exc

        if dry_run:
            plan = build_seed_plan(targets, managers_by_name)
            if output_format == "json":
                self.stdout.write(
                    json.dumps(
                        [
                            {
                                "manager_name": row.manager_name,
                                "target_count": row.target_count,
                                "missing_dependencies": list(row.missing_dependencies),
                            }
                            for row in plan
                        ]
                    )
                )
                return

            for row in plan:
                missing = (
                    f" missing_dependencies={','.join(row.missing_dependencies)}"
                    if row.missing_dependencies
                    else ""
                )
                self.stdout.write(
                    f"{row.manager_name} target={row.target_count}{missing}"
                )
            return

        try:
            result = execute_seed_plan(
                targets=targets,
                managers_by_name=managers_by_name,
                batch_size=batch_size,
                continue_on_error=continue_on_error,
            )
        except (ManagerSelectionError, ManagerSeedFailure) as exc:
            raise CommandError(str(exc)) from exc

        for manager_name, created in result.created.items():
            self.stdout.write(f"{manager_name} created={created}")

        if result.failures:
            summary = "; ".join(
                f"{failure.manager_name}: {failure.error} "
                f"(created={failure.created_count}, "
                f"remaining={failure.remaining_count}, "
                f"batch_size={failure.batch_size})"
                for failure in result.failures
            )
            message = _format_failure_summary(summary)
            self.stderr.write(message)
            raise CommandError(FAILURE_COMMAND_ERROR)

        self.stdout.write(self.style.SUCCESS("Manager landscape seeding complete."))
