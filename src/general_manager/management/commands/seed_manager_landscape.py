"""Seed selected GeneralManager classes through their factories."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from general_manager.manager.meta import GeneralManagerMeta
from general_manager.seeding.manager_landscape import (
    InvalidSeedTargetError,
    ManagerSeedFailure,
    ManagerSelectionError,
    build_seed_plan,
    discover_seedable_managers,
    execute_seed_plan,
    parse_target_overrides,
    select_seed_targets,
)


def _format_failure_summary(summary: str) -> str:
    return f"Seeding completed with failures: {summary}"


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
            "--continue-on-error",
            action="store_true",
            dest="continue_on_error",
            help="Continue with later managers after a manager fails.",
        )

    def handle(self, *_args: Any, **options: Any) -> None:
        try:
            overrides = parse_target_overrides(options["targets"])
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
        except (InvalidSeedTargetError, ManagerSelectionError) as exc:
            raise CommandError(str(exc)) from exc

        if options["dry_run"]:
            for row in build_seed_plan(targets, managers_by_name):
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
                f"{failure.manager_name}: {failure.error}"
                for failure in result.failures
            )
            raise CommandError(_format_failure_summary(summary))

        self.stdout.write(self.style.SUCCESS("Manager landscape seeding complete."))
