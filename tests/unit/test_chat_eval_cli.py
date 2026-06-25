from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from general_manager.chat.evals import __main__ as eval_cli
from general_manager.chat.settings import get_chat_settings


class _Provider:
    def __init__(self) -> None:
        self.config = dict(get_chat_settings().get("provider_config", {}))


class _OtherProvider:
    pass


def test_instantiate_provider_applies_model_override() -> None:
    provider = eval_cli._instantiate_provider(_Provider, "demo-model")

    assert isinstance(provider, _Provider)
    assert provider.config["model"] == "demo-model"


def test_instantiate_provider_without_model_uses_current_settings() -> None:
    provider = eval_cli._instantiate_provider(_Provider)

    assert isinstance(provider, _Provider)


def test_main_runs_default_provider_with_fixture_filters_and_trace(capsys, tmp_path):
    trace_path = tmp_path / "trace.jsonl"
    result = SimpleNamespace(passed=True)
    writer = object()

    with (
        patch("django.setup") as django_setup,
        patch(
            "general_manager.chat.evals.fixtures.setup_toy_schema"
        ) as setup_toy_schema,
        patch(
            "general_manager.chat.evals.traces.EvalTraceWriter",
            return_value=writer,
        ) as trace_writer_cls,
        patch(
            "general_manager.chat.settings.import_provider",
            return_value=_Provider,
        ) as import_provider,
        patch(
            "general_manager.chat.evals.runner.run_eval_suite_sync",
            return_value=[result],
        ) as run_eval_suite_sync,
        patch(
            "general_manager.chat.evals.runner.print_report",
            return_value="all good",
        ) as print_report,
    ):
        eval_cli.main(
            [
                "--fixture",
                "toy",
                "--dataset",
                "basic_queries",
                "--tier",
                "1",
                "--tag",
                "smoke",
                "--trace-jsonl",
                str(trace_path),
                "--verbose",
            ]
        )

    provider = run_eval_suite_sync.call_args.args[0]
    assert isinstance(provider, _Provider)
    assert run_eval_suite_sync.call_args.args[1] == ["basic_queries"]
    assert run_eval_suite_sync.call_args.kwargs == {
        "trace_writer": writer,
        "tier": 1,
        "tags": ["smoke"],
    }
    django_setup.assert_called_once_with()
    setup_toy_schema.assert_called_once_with()
    trace_writer_cls.assert_called_once_with(str(trace_path))
    import_provider.assert_called_once_with()
    print_report.assert_called_once_with([result], verbose=True)
    assert capsys.readouterr().out == "all good\n"


def test_main_compare_mode_runs_each_provider_and_prints_report(capsys):
    first_result = SimpleNamespace(passed=True)
    second_result = SimpleNamespace(passed=False)

    with (
        patch("django.setup"),
        patch(
            "general_manager.chat.evals.fixtures.setup_large_schema"
        ) as setup_large_schema,
        patch(
            "django.utils.module_loading.import_string",
            side_effect=[_Provider, _OtherProvider],
        ) as import_string,
        patch(
            "general_manager.chat.evals.runner.run_eval_suite_sync",
            side_effect=[[first_result], [second_result]],
        ) as run_eval_suite_sync,
        patch(
            "general_manager.chat.evals.runner.print_compare_report",
            return_value="comparison",
        ) as print_compare_report,
    ):
        eval_cli.main(
            [
                "--fixture",
                "large",
                "--compare",
                "demo.FirstProvider,demo.SecondProvider",
                "--model",
                "demo-model",
                "--tag",
                "regression",
            ]
        )

    setup_large_schema.assert_called_once_with()
    assert import_string.call_args_list[0].args == ("demo.FirstProvider",)
    assert import_string.call_args_list[1].args == ("demo.SecondProvider",)
    assert run_eval_suite_sync.call_count == 2
    assert run_eval_suite_sync.call_args_list[0].args[1] is None
    assert run_eval_suite_sync.call_args_list[0].kwargs["tags"] == ["regression"]
    print_compare_report.assert_called_once_with(
        {"FirstProvider": [first_result], "SecondProvider": [second_result]}
    )
    assert capsys.readouterr().out == "comparison\n"


def test_main_uses_explicit_provider_path_and_exits_on_failed_result(capsys):
    result = SimpleNamespace(passed=False)

    with (
        patch("django.setup"),
        patch(
            "django.utils.module_loading.import_string",
            return_value=_Provider,
        ) as import_string,
        patch(
            "general_manager.chat.evals.runner.run_eval_suite_sync",
            return_value=[result],
        ),
        patch(
            "general_manager.chat.evals.runner.print_report",
            return_value="failed",
        ),
    ):
        with pytest.raises(SystemExit) as exit_info:
            eval_cli.main(["--provider", "demo.Provider"])

    import_string.assert_called_once_with("demo.Provider")
    assert exit_info.value.code == 1
    assert capsys.readouterr().out == "failed\n"
