from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch

import pytest

from general_manager.bucket import indexing as indexing_module
from general_manager.bucket.indexing import (
    build_multi_bucket_index,
    build_unique_bucket_index,
)
from tests.perf.support import Counter, PerfBudgets

pytestmark = pytest.mark.perf


@dataclass(frozen=True)
class IndexRow:
    code: str
    group: str


@pytest.mark.parametrize("size", [1_000, 10_000])
def test_bucket_index_single_field_fast_path_preserves_scaling(
    perf_budgets: PerfBudgets,
    size: int,
) -> None:
    rows = [IndexRow(f"code-{index}", f"group-{index % 10}") for index in range(size)]
    single_resolutions = Counter()
    composite_resolutions = Counter()
    original_resolve = indexing_module._resolve_normalized_bucket_index_key
    original_normalize = indexing_module.normalize_bucket_index_key_spec

    def counted_resolve(row, field_names, composite):
        if composite:
            composite_resolutions.increment()
        else:
            single_resolutions.increment()
        return original_resolve(row, field_names, composite)

    with (
        patch.object(
            indexing_module,
            "_resolve_normalized_bucket_index_key",
            side_effect=counted_resolve,
        ),
        patch.object(
            indexing_module,
            "normalize_bucket_index_key_spec",
            wraps=original_normalize,
        ) as normalizations,
    ):
        unique_single = build_unique_bucket_index(rows, "code", max_rows=None)
        multi_single = build_multi_bucket_index(rows, "code", max_rows=None)
        unique_composite = build_unique_bucket_index(
            rows,
            ("code", "group"),
            max_rows=None,
        )
        multi_composite = build_multi_bucket_index(
            rows,
            ("code", "group"),
            max_rows=None,
        )

    assert len(unique_single) == size
    assert len(multi_single) == size
    assert len(unique_composite) == size
    assert len(multi_composite) == size
    assert single_resolutions.value == size * 2
    assert composite_resolutions.value == size * 2
    assert normalizations.call_count == 4
    prefix = f"BUCKET_INDEX_{size}"
    perf_budgets.assert_observation(
        f"{prefix}_SINGLE_RESOLUTIONS", single_resolutions.value
    )
    perf_budgets.assert_observation(
        f"{prefix}_COMPOSITE_RESOLUTIONS", composite_resolutions.value
    )
    perf_budgets.assert_observation(
        f"{prefix}_NORMALIZATIONS", normalizations.call_count
    )
    perf_budgets.assert_observation(f"{prefix}_SINGLE_KEYS", len(unique_single))
    perf_budgets.assert_observation(f"{prefix}_COMPOSITE_KEYS", len(unique_composite))
