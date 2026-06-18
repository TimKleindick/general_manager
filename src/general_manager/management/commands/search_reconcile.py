from __future__ import annotations

import argparse
import time
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from general_manager.search.reconciliation import reconcile_search_indexes


class InvalidSearchReconcileModeError(CommandError):
    def __init__(self) -> None:
        super().__init__("Pass exactly one of --once or --watch.")


class PositiveIntegerArgumentError(argparse.ArgumentTypeError):
    def __init__(self) -> None:
        super().__init__("must be a positive integer")


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise PositiveIntegerArgumentError from exc
    if parsed <= 0:
        raise PositiveIntegerArgumentError
    return parsed


class Command(BaseCommand):
    help = "Reconcile dirty search indexes."

    def add_arguments(self, parser) -> None:  # type: ignore[override]
        parser.add_argument(
            "--once", action="store_true", help="Run one sweep and exit."
        )
        parser.add_argument("--watch", action="store_true", help="Run continuously.")
        parser.add_argument(
            "--interval",
            type=float,
            default=60.0,
            help="Watch interval in seconds.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Mark all configured search states dirty before reconciling.",
        )
        parser.add_argument(
            "--max-states",
            type=positive_int,
            default=None,
            help="Maximum dirty states to reconcile per sweep.",
        )

    def handle(self, *_: Any, **options: Any) -> None:
        once = bool(options["once"])
        watch = bool(options["watch"])
        if once == watch:
            raise InvalidSearchReconcileModeError

        interval = max(1.0, float(options["interval"]))
        force = bool(options["force"])
        max_states = options["max_states"]

        while True:
            result = reconcile_search_indexes(force=force, max_states=max_states)
            self.stdout.write(
                self.style.SUCCESS(
                    "Reconciled "
                    f"{result.reconciled} search index states "
                    f"({result.documents} documents, {result.failed} failures)."
                )
            )
            if once:
                return
            force = False
            time.sleep(interval)
