from __future__ import annotations

from django.test import SimpleTestCase

from general_manager.search.backend import SearchDocument
from general_manager.search.backends.dev import DevSearchBackend
from general_manager.bucket._ordering import InvalidOrderingError
from general_manager.measurement import Measurement


class DevSearchBackendTests(SimpleTestCase):
    def setUp(self) -> None:
        """
        Prepare a DevSearchBackend with a "global" index containing two sample Project documents used by the tests.

        The backend is instantiated, an index named "global" is ensured, and two SearchDocument entries are upserted:
        - "Project:1": name "Alpha Project", status "public", tags ["a"], name field_boost 2.0
        - "Project:2": name "Beta Project", status "private", tags ["b"], name field_boost 1.0
        """
        self.backend = DevSearchBackend()
        self.backend.ensure_index("global", {})
        self.backend.upsert(
            "global",
            [
                SearchDocument(
                    id="Project:1",
                    type="Project",
                    identification={"id": 1},
                    index="global",
                    data={"name": "Alpha Project", "status": "public", "tags": ["a"]},
                    field_boosts={"name": 2.0},
                ),
                SearchDocument(
                    id="Project:2",
                    type="Project",
                    identification={"id": 2},
                    index="global",
                    data={"name": "Beta Project", "status": "private", "tags": ["b"]},
                    field_boosts={"name": 1.0},
                ),
            ],
        )

    def test_search_with_filter_groups(self) -> None:
        """
        Verify that using multiple filter groups returns documents matching any of the groups.

        Search the "global" index with filters [{"status": "public"}, {"tags__in": ["b"]}] and assert the total number of matching documents is 2.
        """
        result = self.backend.search(
            "global",
            "",
            filters=[{"status": "public"}, {"tags__in": ["b"]}],
        )
        assert result.total == 2

    def test_list_field_in_filter_with_scalar_value_returns_no_hits(self) -> None:
        """Treat invalid scalar `in` filters for list-valued fields as false."""
        result = self.backend.search("global", "", filters={"tags__in": "a"})
        assert result.total == 0

    def test_filter_groups_reject_scalar_sequences(self) -> None:
        result = self.backend.search("global", "", filters="status")

        assert result.total == 0

    def test_type_restriction_conjoins_every_filter_group(self) -> None:
        self.backend.upsert(
            "global",
            [
                SearchDocument(
                    id="OtherProject:3",
                    type="OtherProject",
                    identification={"id": 3},
                    index="global",
                    data={"name": "Gamma Project", "status": "private"},
                    field_boosts={"name": 1.0},
                )
            ],
        )
        result = self.backend.search(
            "global",
            "",
            filters=[{"status": "public"}, {"status": "private"}],
            types=["Project"],
        )

        assert [hit.id for hit in result.hits] == ["Project:1", "Project:2"]

    def test_search_sorting(self) -> None:
        """Sort search results by a stored document field."""
        result = self.backend.search("global", "", sort=("-name",))
        data = result.hits[0].data
        assert data is not None
        assert data["name"] == "Beta Project"

    def test_search_rejects_scalar_sort_string(self) -> None:
        with self.assertRaises(InvalidOrderingError):
            self.backend.search("global", "", sort="name")
        with self.assertRaises(InvalidOrderingError):
            self.backend.search("global", "", sort="")

    def test_search_rejects_plus_prefixed_sort_field(self) -> None:
        with self.assertRaises(InvalidOrderingError):
            self.backend.search("global", "", sort=("+name",))

    def test_search_mixed_direction_keeps_nulls_last(self) -> None:
        """Mixed signed sort terms preserve their directions and null placement."""
        self.backend.ensure_index("ordering", {})
        self.backend.upsert(
            "ordering",
            [
                SearchDocument(
                    "Project:1",
                    "Project",
                    {"id": 1},
                    "ordering",
                    {"name": "a", "date": 1},
                    {},
                ),
                SearchDocument(
                    "Project:2",
                    "Project",
                    {"id": 2},
                    "ordering",
                    {"name": "a", "date": 2},
                    {},
                ),
                SearchDocument(
                    "Project:3",
                    "Project",
                    {"id": 3},
                    "ordering",
                    {"name": "b", "date": None},
                    {},
                ),
            ],
        )

        result = self.backend.search("ordering", "", sort=("name", "-date"))

        assert [hit.identification["id"] for hit in result.hits] == [2, 1, 3]

    def test_explicit_sort_ties_use_typed_logical_identity_before_document_id(
        self,
    ) -> None:
        self.backend.ensure_index("logical-identity", {})
        self.backend.upsert(
            "logical-identity",
            [
                SearchDocument(
                    str(row_id),
                    "Project",
                    {"id": row_id},
                    "logical-identity",
                    {"name": "same"},
                    {},
                )
                for row_id in (10, 2, 1)
            ],
        )

        full_result = self.backend.search(
            "logical-identity", "", sort=("name",), limit=10
        )
        limited_result = self.backend.search(
            "logical-identity", "", sort=("name",), limit=2
        )

        assert [hit.identification["id"] for hit in full_result.hits] == [1, 2, 10]
        assert [hit.identification["id"] for hit in limited_result.hits] == [1, 2]

    def test_search_preserves_date_looking_strings_as_strings(self) -> None:
        self.backend.ensure_index("dates", {})
        self.backend.upsert(
            "dates",
            [
                SearchDocument(
                    "Project:1",
                    "Project",
                    {"id": 1},
                    "dates",
                    {"date": "2024-2-01"},
                    {},
                ),
                SearchDocument(
                    "Project:2",
                    "Project",
                    {"id": 2},
                    "dates",
                    {"date": "2024-10-01"},
                    {},
                ),
            ],
        )

        result = self.backend.search("dates", "", sort=("date",))

        assert [hit.identification["id"] for hit in result.hits] == [2, 1]

    def test_search_orders_heterogeneous_values_by_category(self) -> None:
        self.backend.ensure_index("mixed-values", {})
        self.backend.upsert(
            "mixed-values",
            [
                SearchDocument(
                    "Project:1",
                    "Project",
                    {"id": 1},
                    "mixed-values",
                    {"value": "later"},
                    {},
                ),
                SearchDocument(
                    "Project:2", "Project", {"id": 2}, "mixed-values", {"value": 2}, {}
                ),
                SearchDocument(
                    "Project:3",
                    "Project",
                    {"id": 3},
                    "mixed-values",
                    {"value": False},
                    {},
                ),
            ],
        )

        result = self.backend.search("mixed-values", "", sort=("value",))

        assert [hit.identification["id"] for hit in result.hits] == [3, 2, 1]

    def test_search_preserves_large_integer_precision_when_sorting(self) -> None:
        self.backend.ensure_index("large-integers", {})
        self.backend.upsert(
            "large-integers",
            [
                SearchDocument(
                    "Project:1",
                    "Project",
                    {"id": 1},
                    "large-integers",
                    {"value": 2**53 + 1},
                    {},
                ),
                SearchDocument(
                    "Project:2",
                    "Project",
                    {"id": 2},
                    "large-integers",
                    {"value": 2**53},
                    {},
                ),
            ],
        )

        result = self.backend.search("large-integers", "", sort=("value",))

        assert [hit.identification["id"] for hit in result.hits] == [2, 1]

    def test_search_keeps_homogeneous_measurements_comparable(self) -> None:
        self.backend.ensure_index("measurements", {})
        self.backend.upsert(
            "measurements",
            [
                SearchDocument(
                    "Project:1",
                    "Project",
                    {"id": 1},
                    "measurements",
                    {"value": Measurement(2, "kg")},
                    {},
                ),
                SearchDocument(
                    "Project:2",
                    "Project",
                    {"id": 2},
                    "measurements",
                    {"value": Measurement(1, "kg")},
                    {},
                ),
            ],
        )

        result = self.backend.search("measurements", "", sort=("value",))

        assert [hit.identification["id"] for hit in result.hits] == [2, 1]

    def test_search_requires_every_distinct_query_term(self) -> None:
        result = self.backend.search("global", "Alpha missing")
        assert result.total == 0

    def test_search_allows_query_terms_to_match_different_fields(self) -> None:
        result = self.backend.search("global", "Alpha public")
        assert [hit.id for hit in result.hits] == ["Project:1"]

    def test_repeated_query_terms_do_not_inflate_score(self) -> None:
        single = self.backend.search("global", "Alpha")
        repeated = self.backend.search("global", "Alpha Alpha")
        assert repeated.hits[0].score == single.hits[0].score

    def test_list_document_ids_filters_by_type(self) -> None:
        """Return indexed document IDs restricted to requested type labels."""
        assert self.backend.list_document_ids("global", types=["Project"]) == {
            "Project:1",
            "Project:2",
        }
        assert self.backend.list_document_ids("global", types=["OtherProject"]) == set()
