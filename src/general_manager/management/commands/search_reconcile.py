from __future__ import annotations

import argparse
import time

from django.core.management.base import BaseCommand, CommandError

from general_manager.search.reconciliation import reconcile_search_indexes


class InvalidSearchReconcileModeError(CommandError):
    def __init__(self) -> None:
        """Initialize the error raised for missing or conflicting run modes."""
        super().__init__("Pass exactly one of --once or --watch.")


class PositiveIntegerArgumentError(argparse.ArgumentTypeError):
    def __init__(self) -> None:
        """Initialize the parser error raised for non-positive integers."""
        super().__init__("must be a positive integer")


class InvalidSearchReconcileOptionError(CommandError):
    def __init__(self, option_name: str) -> None:
        """Initialize the error raised for invalid programmatic option values."""
        super().__init__(f"{option_name} must be a positive number")


def positive_int(value: str) -> int:
    """Parse a command-line value as an integer greater than zero."""
    try:
        parsed = int(value)
    except ValueError as exc:
        raise PositiveIntegerArgumentError from exc
    if parsed <= 0:
        raise PositiveIntegerArgumentError
    return parsed


class Command(BaseCommand):
    help = "Reconcile dirty search indexes."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """
        Register command-line options for search reconciliation.

        The command requires exactly one of `--once` or `--watch`. `--force`
        marks all configured states dirty for the next sweep, `--max-states`
        limits one sweep to a positive number of dirty states, and `--interval`
        controls the watch delay in seconds.
        """
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

    def handle(self, *_args: object, **options: object) -> None:
        """
        Run one reconciliation sweep or watch continuously.

        Raises:
            InvalidSearchReconcileModeError: If neither or both run modes are selected.
            InvalidSearchReconcileOptionError: If programmatic option values are invalid.
            Exception: Reconciliation service and sleep interruption errors propagate.
        """
        once = bool(options.get("once"))
        watch = bool(options.get("watch"))
        if once == watch:
            raise InvalidSearchReconcileModeError()

        interval = _positive_float_option(options.get("interval", 60.0), "interval")
        force = bool(options.get("force"))
        max_states = _positive_int_option(options.get("max_states"), "max_states")

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


def _positive_float_option(value: object, option_name: str) -> float:
    """Normalize a positive float command option, clamped to at least one."""
    if isinstance(value, bool):
        raise InvalidSearchReconcileOptionError(option_name)
    if isinstance(value, int | float | str):
        try:
            return max(1.0, float(value))
        except ValueError as exc:
            raise InvalidSearchReconcileOptionError(option_name) from exc
    raise InvalidSearchReconcileOptionError(option_name)


def _positive_int_option(value: object, option_name: str) -> int | None:
    """Normalize an optional positive integer command option."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise InvalidSearchReconcileOptionError(option_name)
    if isinstance(value, int):
        if value > 0:
            return value
        raise InvalidSearchReconcileOptionError(option_name)
    if isinstance(value, str):
        try:
            return positive_int(value)
        except argparse.ArgumentTypeError as exc:
            raise InvalidSearchReconcileOptionError(option_name) from exc
    raise InvalidSearchReconcileOptionError(option_name)
