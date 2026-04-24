"""Seed selected GeneralManager classes through their factories."""

from __future__ import annotations

import json
from typing import Any

from django.core.management.base import BaseCommand, CommandError

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


def _format_failure_summary(summary: str) -> str:
    return f"{FAILURE_COMMAND_ERROR}: {summary}"


class Command(BaseCommand):
    help = "Seed selected GeneralManager classes using their factories."

    def add_arguments(self, parser: Any) -> None:
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

    def handle(self, *_args: Any, **options: Any) -> None:
        """Run manager seeding with explicit selection and predictable exits.

        ``--all`` and ``--manager`` are mutually exclusive; seeding stops at the
        first failure unless ``--continue-on-error`` is set. Successful runs exit
        normally, while validation or seeding errors raise ``CommandError``.
        """

        try:
            overrides = parse_target_overrides(options["targets"])
            if options["include_all"] and options["managers"]:
                raise ManagerSelectionError.conflicting_selection()
            managers_by_name = discover_seedable_managers(
                GeneralManagerMeta.all_classes
            )
            targets = select_seed_targets(
                managers_by_name=managers_by_name,
                selected_names=options["managers"],
                include_all=bool(options["include_all"]),
                default_count=int(options["count"]),
                overrides=overrides,
            )
        except (
            InvalidSeedTargetError,
            ManagerSelectionError,
            SeedableManagerCollisionError,
        ) as exc:
            raise CommandError(str(exc)) from exc

        if options["dry_run"]:
            plan = build_seed_plan(targets, managers_by_name)
            if options["output_format"] == "json":
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
                batch_size=int(options["batch_size"]),
                continue_on_error=bool(options["continue_on_error"]),
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
