"""CLI entry point: ``python -m general_manager.chat.evals``."""

from __future__ import annotations

import argparse
import os
import sys


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Run the GeneralManager chat evaluation suite.",
    )
    parser.add_argument(
        "--provider",
        default=None,
        help="Dotted path to the provider class (default: settings provider).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model name passed to the provider config.",
    )
    parser.add_argument(
        "--dataset",
        default=None,
        help="Run only the named dataset (e.g. basic_queries).",
    )
    parser.add_argument(
        "--compare",
        default=None,
        help="Comma-separated provider paths for side-by-side comparison.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show detailed failure information.",
    )
    parser.add_argument(
        "--settings",
        default=None,
        help="Django settings module.",
    )
    args = parser.parse_args(argv)

    if args.settings:
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", args.settings)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "")

    import django

    django.setup()

    from general_manager.chat.evals.runner import (
        print_compare_report,
        print_report,
        run_eval_suite_sync,
    )
    from general_manager.chat.settings import import_provider

    dataset_names = [args.dataset] if args.dataset else None

    if args.compare:
        from django.utils.module_loading import import_string

        provider_paths = [p.strip() for p in args.compare.split(",")]
        results_by_provider: dict[str, list] = {}
        for path in provider_paths:
            provider_cls = import_string(path)
            provider = _instantiate_provider(provider_cls, args.model)
            results = run_eval_suite_sync(provider, dataset_names)
            label = path.rsplit(".", 1)[-1]
            results_by_provider[label] = results
        report = print_compare_report(results_by_provider)
        print(report)
        return

    if args.provider:
        from django.utils.module_loading import import_string

        provider_cls = import_string(args.provider)
    else:
        provider_cls = import_provider()

    provider = _instantiate_provider(provider_cls, args.model)
    results = run_eval_suite_sync(provider, dataset_names)
    report = print_report(results, verbose=args.verbose)
    print(report)

    all_passed = all(r.passed for r in results)
    if not all_passed:
        sys.exit(1)


def _instantiate_provider(provider_cls: type, model: str | None = None) -> object:
    """Instantiate a provider, applying model override via settings if needed."""
    if model is not None:
        from django.test.utils import override_settings

        from general_manager.chat.settings import get_chat_settings

        settings = get_chat_settings()
        provider_config = dict(settings.get("provider_config", {}))
        provider_config["model"] = model
        chat = dict(settings)
        chat["provider_config"] = provider_config
        with override_settings(GENERAL_MANAGER={"CHAT": chat}):
            return provider_cls()
    return provider_cls()


if __name__ == "__main__":
    main()
