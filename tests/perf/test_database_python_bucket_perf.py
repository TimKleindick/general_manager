from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.db import connection
from django.test.utils import CaptureQueriesContext

from general_manager.bucket.database_bucket import DatabaseBucket
from general_manager.cache.run_context import CalculationRunContext
from tests.perf.support import PerfBudgets
from tests.unit.test_database_bucket import (
    PythonPropertyInterface,
    PythonPropertyManager,
)

pytestmark = [pytest.mark.perf, pytest.mark.django_db]


def test_bounded_python_filter_uses_one_query_and_compiles_once(
    perf_budgets: PerfBudgets,
) -> None:
    PythonPropertyManager.Interface = PythonPropertyInterface
    PythonPropertyInterface._parent_class = PythonPropertyManager
    User.objects.bulk_create(
        [User(username=f"python-perf-{index}") for index in range(100)]
    )
    bucket = DatabaseBucket(User.objects.order_by("id"), PythonPropertyManager)

    with (
        CalculationRunContext(),
        CaptureQueriesContext(connection) as queries,
        patch(
            "general_manager.bucket.database_bucket.create_filter_function"
        ) as factory,
    ):
        factory.side_effect = lambda lookup, value: __import__(
            "general_manager.utils.filter_parser",
            fromlist=["create_filter_function"],
        ).create_filter_function(lookup, value)
        filtered = bucket.filter(python_username_length__gte=1)
        list(filtered)

    assert len(queries) == 1
    assert factory.call_count == 1
    perf_budgets.assert_observation("DB_PYTHON_FILTER_QUERIES", len(queries))
    perf_budgets.assert_observation("DB_PYTHON_FILTER_PREDICATES", factory.call_count)


def test_bounded_python_sort_uses_one_query(perf_budgets: PerfBudgets) -> None:
    PythonPropertyManager.Interface = PythonPropertyInterface
    PythonPropertyInterface._parent_class = PythonPropertyManager
    User.objects.bulk_create(
        [User(username=f"python-sort-{index}") for index in range(100)]
    )
    bucket = DatabaseBucket(User.objects.order_by("id"), PythonPropertyManager)

    with CalculationRunContext(), CaptureQueriesContext(connection) as queries:
        sorted_bucket = bucket.sort("python_username_length", reverse=True)
        list(sorted_bucket)

    assert len(queries) == 1
    perf_budgets.assert_observation("DB_PYTHON_SORT_QUERIES", len(queries))


def test_large_python_sort_keeps_legacy_case_fallback(
    perf_budgets: PerfBudgets,
) -> None:
    PythonPropertyManager.Interface = PythonPropertyInterface
    PythonPropertyInterface._parent_class = PythonPropertyManager
    User.objects.bulk_create(
        [User(username=f"python-large-{index}") for index in range(1001)]
    )
    bucket = DatabaseBucket(User.objects.order_by("id"), PythonPropertyManager)

    with CalculationRunContext(), CaptureQueriesContext(connection) as queries:
        sorted_bucket = bucket.sort("python_username_length")
        for _manager in sorted_bucket:
            pass

    assert len(queries) == 2
    perf_budgets.assert_observation(
        "DB_PYTHON_SORT_LARGE_FALLBACK_QUERIES", len(queries)
    )
