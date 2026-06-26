from __future__ import annotations

from unittest.mock import patch

import pytest

from general_manager.api.graphql import GraphQL


def test_reset_registry_surfaces_schema_index_cache_clear_errors() -> None:
    with patch(
        "general_manager.chat.schema_index.clear_schema_index_cache",
        side_effect=RuntimeError("cache clear failed"),
    ):
        with pytest.raises(RuntimeError, match="cache clear failed"):
            GraphQL.reset_registry()
