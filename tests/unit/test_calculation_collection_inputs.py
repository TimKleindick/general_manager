"""Atomic typed-list selections in calculation buckets."""

import pytest

from general_manager.bucket.calculation_bucket import InvalidPossibleValuesError
from general_manager.interface import CalculationInterface
from general_manager.manager import GeneralManager, Input


class CollectionSummary(GeneralManager):
    class Interface(CalculationInterface):
        selections = Input(list[int], required=False)


class CollectionDomainSummary(GeneralManager):
    class Interface(CalculationInterface):
        selections = Input(list[int], possible_values=[[], [1, 2], [2, 1], [1, 1]])


class RequiredCollectionSummary(GeneralManager):
    class Interface(CalculationInterface):
        selections = Input(list[int])


@pytest.mark.parametrize("selection", [None, [], [1, 2], [2, 1, 2]])
def test_exact_selection_is_one_complete_input(selection):
    items = list(CollectionSummary.filter(selections=selection))
    assert len(items) == 1
    assert items[0].selections == selection


def test_in_selects_alternatives_of_whole_lists():
    items = list(CollectionSummary.filter(selections__in=[[], [1, 2], [2, 1], None]))
    assert [item.selections for item in items] == [[], [1, 2], [2, 1], None]


def test_empty_in_has_no_candidates():
    assert list(CollectionSummary.filter(selections__in=[])) == []


def test_optional_domainless_selection_defaults_to_none():
    assert [item.selections for item in CollectionSummary.all()] == [None]


def test_required_domainless_selection_needs_exact_filter():
    with pytest.raises(InvalidPossibleValuesError):
        list(RequiredCollectionSummary.all())
    assert RequiredCollectionSummary.filter(selections=[]).get().selections == []


def test_explicit_domains_contain_atomic_lists():
    assert [item.selections for item in CollectionDomainSummary.all()] == [
        [],
        [1, 2],
        [2, 1],
        [1, 1],
    ]
    assert [
        item.selections
        for item in CollectionDomainSummary.filter(selections__in=[[1, 2], []])
    ] == [[], [1, 2]]


def test_exclude_compares_complete_lists():
    assert [
        item.selections for item in CollectionDomainSummary.exclude(selections=[])
    ] == [[1, 2], [2, 1], [1, 1]]


@pytest.mark.parametrize("lookup", ["contains", "gt", "id", "name__exact"])
def test_unsupported_collection_lookups_are_rejected(lookup):
    with pytest.raises(ValueError, match=r"collection.*exact.*in"):
        CollectionSummary.filter(**{f"selections__{lookup}": [1]})


def test_in_requires_alternatives_of_lists():
    with pytest.raises((TypeError, ValueError)):
        CollectionSummary.filter(selections__in=[1, 2])


def test_exact_filter_casts_elements():
    assert CollectionSummary.filter(selections=["1", "2"]).get().selections == [1, 2]


def test_chained_filters_intersect_atomic_selections():
    items = CollectionSummary.filter(selections__in=[[], [1, 2]]).filter(selections=[])
    assert [item.selections for item in items] == [[]]


def test_collection_plan_signatures_are_hashable_and_order_sensitive():
    first = CollectionSummary.filter(selections=[1, 2])._bucket_index_source_signature()
    equivalent = CollectionSummary.filter(
        selections=[1, 2]
    )._bucket_index_source_signature()
    reversed_selection = CollectionSummary.filter(
        selections=[2, 1]
    )._bucket_index_source_signature()
    assert hash(first) == hash(equivalent)
    assert first != reversed_selection


class CollectionMember(GeneralManager):
    class Interface(CalculationInterface):
        id = Input(int, possible_values=[1, 2])


class ManagerCollectionSummary(GeneralManager):
    class Interface(CalculationInterface):
        members = Input(list[CollectionMember], required=False)


def test_manager_collection_id_alias_binds_complete_selection():
    item = ManagerCollectionSummary.filter(members_id=[2, 1, 2]).get()
    assert [member.id for member in item.members] == [2, 1, 2]


def test_manager_collection_in_casts_each_alternative():
    items = ManagerCollectionSummary.filter(members_id__in=[[], [1, 2]])
    assert [[member.id for member in item.members] for item in items] == [[], [1, 2]]


def test_manager_collection_exact_matches_domain_of_instances():
    class DomainSummary(GeneralManager):
        class Interface(CalculationInterface):
            members = Input(
                list[CollectionMember],
                possible_values=[[], [CollectionMember(1), CollectionMember(2)]],
            )

    assert [
        [member.id for member in item.members]
        for item in DomainSummary.filter(members_id=[1, 2])
    ] == [[1, 2]]


def test_collection_domain_dependencies_receive_whole_selection():
    class DependentSummary(GeneralManager):
        class Interface(CalculationInterface):
            selections = Input(list[int], possible_values=[[], [1, 2]])
            count = Input(int, possible_values=lambda selections: [len(selections)])

    assert [(item.selections, item.count) for item in DependentSummary.all()] == [
        ([], 0),
        ([1, 2], 2),
    ]


def test_scalar_in_still_enumerates_individual_values():
    assert [member.id for member in CollectionMember.filter(id__in=[1, 2])] == [1, 2]


def test_whole_list_normalizer_runs_once_at_construction():
    calls = []

    def normalize(values):
        calls.append(list(values))
        return [*values, 9]

    class NormalizedSummary(GeneralManager):
        class Interface(CalculationInterface):
            selections = Input(list[int], normalizer=normalize)

    item = NormalizedSummary.filter(selections=[1]).get()
    assert item.selections == [1, 9]
    assert calls == [[1]]


@pytest.mark.parametrize(
    "operation", ["repeated", "property", "sort", "projection", "slice"]
)
def test_normalized_collection_survives_bucket_operations(operation):
    from general_manager.api.property import graph_ql_property

    calls = []

    def normalize(values):
        calls.append(list(values))
        return [*values, 9]

    class NormalizedBucketSummary(GeneralManager):
        class Interface(CalculationInterface):
            selections = Input(list[int], normalizer=normalize)

        @graph_ql_property(sortable=True)
        def count_values(self) -> int:
            return len(self.selections)

    bucket = NormalizedBucketSummary.filter(selections=[1])
    if operation == "repeated":
        bucket = bucket.filter(selections=[1])
    elif operation == "property":
        bucket = bucket.filter(count_values=2)
    elif operation == "sort":
        bucket = bucket.sort("count_values")
    elif operation == "projection":
        assert bucket.values_list("selections", flat=True) == ([1, 9],)
    elif operation == "slice":
        bucket = bucket[:1]
    assert [item.selections for item in bucket] == [[1, 9]]
    assert calls == [[1]]


def test_collection_filter_normalizer_receives_dependencies():
    class DependentNormalizedSummary(GeneralManager):
        class Interface(CalculationInterface):
            addition = Input(int, possible_values=[8, 9])
            selections = Input(
                list[int],
                depends_on=["addition"],
                normalizer=lambda values, addition: [*values, addition],
            )

    items = DependentNormalizedSummary.filter(selections=[1]).filter(selections=[1])
    assert [(item.addition, item.selections) for item in items] == [
        (8, [1, 8]),
        (9, [1, 9]),
    ]


def test_dependent_domain_receives_normalized_collection():
    class NormalizedDomainSummary(GeneralManager):
        class Interface(CalculationInterface):
            selections = Input(list[int], normalizer=lambda values: [*values, 9])
            count = Input(int, possible_values=lambda selections: [len(selections)])

    item = NormalizedDomainSummary.filter(selections=[1]).get()
    assert item.selections == [1, 9]
    assert item.count == 2


def test_grouped_exclusion_compares_normalized_collection():
    from general_manager.api.property import graph_ql_property

    class ExcludedNormalizedSummary(GeneralManager):
        class Interface(CalculationInterface):
            selections = Input(list[int], normalizer=lambda values: [*values, 9])

        @graph_ql_property
        def count_values(self) -> int:
            return len(self.selections)

    assert (
        list(
            ExcludedNormalizedSummary.filter(selections=[1]).exclude(
                selections=[1], count_values=2
            )
        )
        == []
    )


def test_property_filtered_composite_members_preserve_nested_normalization():
    from general_manager.api.property import graph_ql_property

    class NormalizedMember(GeneralManager):
        class Interface(CalculationInterface):
            selections = Input(list[int], normalizer=lambda values: [*values, 9])

    class CompositeBucketSummary(GeneralManager):
        class Interface(CalculationInterface):
            members = Input(list[NormalizedMember])

        @graph_ql_property
        def member_count(self) -> int:
            return len(self.members)

    member = NormalizedMember(selections=[1])
    bucket = CompositeBucketSummary.filter(members=[member], member_count=1)
    item = bucket.get()
    assert item.members[0].selections == [1, 9]


def test_normalized_equivalent_selections_match_complete_domain():
    class CanonicalSummary(GeneralManager):
        class Interface(CalculationInterface):
            selections = Input(list[int], possible_values=[[1, 2]], normalizer=sorted)

    assert [
        item.selections
        for item in CanonicalSummary.filter(selections=[2, 1]).filter(selections=[1, 2])
    ] == [[1, 2]]


def test_collection_normalizer_uses_normalized_scalar_dependency():
    class ScalarDependencySummary(GeneralManager):
        class Interface(CalculationInterface):
            addition = Input(
                int, normalizer=lambda value: value + 1, possible_values=[1, 2]
            )
            selections = Input(
                list[int],
                depends_on=["addition"],
                normalizer=lambda values, addition: [*values, addition],
            )

    item = ScalarDependencySummary.filter(addition=1, selections=[4]).get()
    assert item.selections == [4, item.addition]
