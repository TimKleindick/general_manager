from typing import Any, List
from unittest.mock import Mock

import pytest
from django.test import override_settings

from general_manager.interface import CalculationInterface
from general_manager.manager import GeneralManager, Input


def summary(field):
    class CollectionSummary(GeneralManager):
        class Interface(CalculationInterface):
            values = field

    return CollectionSummary


@pytest.mark.parametrize("annotation", [list[int], List[int]])
def test_cast_collection(annotation):
    field = Input(annotation)
    assert field.is_collection
    assert field.collection_element_type is int
    assert field.cast(["2", 1, "2"]) == [2, 1, 2]


@pytest.mark.parametrize(
    "annotation",
    [list[list[int]], list[Any], list[int | None], set[int], tuple[int], list[object]],
)
def test_reject_unsupported_declarations(annotation):
    with pytest.raises(TypeError, match="collection"):
        Input(annotation)


@pytest.mark.parametrize("value", ["12", (1, 2), {1, 2}, {"id": 1}, iter([1])])
def test_reject_non_list_containers(value):
    with pytest.raises(TypeError, match="list"):
        Input(list[int]).cast(value)


@pytest.mark.parametrize("value", [[None], ["bad"]])
def test_indexed_cast_errors(value):
    with pytest.raises((TypeError, ValueError), match=r"values\[0\]"):
        summary(Input(list[int]))(values=value)


def test_collection_bounds_rejected():
    with pytest.raises(TypeError, match="collection"):
        Input(list[int], min_value=1)


def test_null_empty_and_requiredness():
    optional = summary(Input(list[int], required=False))
    assert (
        optional().identification
        == optional(values=None).identification
        == {"values": None}
    )
    assert optional(values=[]).values == []
    required = summary(Input(list[int]))
    with pytest.raises(TypeError):
        required()
    with pytest.raises(TypeError):
        required(values=None)


def test_callbacks_once_and_defensive_lists():
    normalizer = Mock(side_effect=lambda value, **_kwargs: [*value, 3])
    validator = Mock(return_value=True)
    manager = summary(Input(list[int], normalizer=normalizer, validator=validator))
    raw = [1, 2]
    instance = manager(values=raw)
    raw.append(9)
    assert instance.values == [1, 2, 3]
    instance.values.append(8)
    instance.identification["values"].append(7)
    assert instance.values == [1, 2, 3]
    assert instance.identification == {"values": [1, 2, 3]}
    assert normalizer.call_count == validator.call_count == 1


@override_settings(DEBUG=True)
def test_whole_list_domain():
    manager = summary(Input(list[int], possible_values=[[], [1, 2]]))
    assert manager(values=[]).values == []
    assert manager(values=[1, 2]).values == [1, 2]
    with pytest.raises(ValueError):
        manager(values=[2, 1])


def test_manager_members_and_composite_identity():
    class Member(GeneralManager):
        class Interface(CalculationInterface):
            region = Input(str)
            number = Input(int)

    class Other(GeneralManager):
        class Interface(CalculationInterface):
            id = Input(int)

    manager = summary(Input(list[Member]))
    member = Member(region="west", number=3)
    instance = manager(values=[member, {"region": "west", "number": "3"}])
    assert instance.values[0] is member
    assert instance.values[1].identification == member.identification
    assert instance.identification == {
        "values": [member.identification, member.identification]
    }
    exposed = instance.identification
    exposed["values"][0]["region"] = "east"
    assert instance.identification["values"][0]["region"] == "west"
    assert manager(**instance.identification).identification == instance.identification
    with pytest.raises(TypeError, match=r"values\[0\]"):
        manager(values=[Other(id=3)])


def test_manager_id_alias_preserves_order_and_duplicates():
    class Member(GeneralManager):
        class Interface(CalculationInterface):
            id = Input(int)

    manager = summary(Input(list[Member]))
    instance = manager(values_id=["2", "1", "2"])
    assert [value.id for value in instance.values] == [2, 1, 2]
    assert instance.identification == {"values": [{"id": 2}, {"id": 1}, {"id": 2}]}


@pytest.mark.parametrize("result", [("bad",), [None], ["bad"]])
@override_settings(DEBUG=False, VALIDATE_INPUT_VALUES=False)
def test_normalizer_shape_always_validated(result):
    manager = summary(Input(list[int], normalizer=lambda _value: result))
    with pytest.raises(TypeError):
        manager(values=[1])


def test_callbacks_receive_resolved_dependencies_and_empty_list():
    calls = []

    def normalize(values, offset):
        calls.append(("normalize", list(values), offset))
        return [value + offset for value in values]

    def validate(values, offset):
        calls.append(("validate", list(values), offset))
        return True

    class DependentSummary(GeneralManager):
        class Interface(CalculationInterface):
            offset = Input(int)
            values = Input(
                list[int],
                depends_on=["offset"],
                normalizer=normalize,
                validator=validate,
            )

    instance = DependentSummary(offset="2", values=[])
    assert instance.values == []
    assert calls == [("normalize", [], 2), ("validate", [], 2)]


def test_member_dependencies_replayed_on_every_access():
    class Member(GeneralManager):
        class Interface(CalculationInterface):
            id = Input(int)

    manager = summary(Input(list[Member]))
    first, second = Member(id=1), Member(id=2)
    instance = manager(values=[first, second, first])
    from unittest.mock import patch

    with patch.object(Member, "_track_identification_dependency") as track:
        assert instance.values == [first, second, first]
        assert instance.values == [first, second, first]
    assert [call.args[0] for call in track.call_args_list] == [
        {"id": 1},
        {"id": 2},
        {"id": 1},
        {"id": 1},
        {"id": 2},
        {"id": 1},
    ]


@pytest.mark.parametrize("values", [[], [1], [1, 2], [2, 1], [1, 1], None])
def test_identification_retains_selection(values):
    manager = summary(Input(list[int], required=False))
    assert manager(values=values).identification == {"values": values}


class PickleCollectionSummary(GeneralManager):
    class Interface(CalculationInterface):
        values = Input(list[int], normalizer=lambda value: [*value, 3])


def test_normalized_collection_pickle_and_deepcopy():
    import pickle
    from copy import deepcopy

    instance = PickleCollectionSummary(values=[1])
    for restored in (deepcopy(instance), pickle.loads(pickle.dumps(instance))):  # noqa: S301
        assert restored.values == [1, 3]
        assert restored.identification == instance.identification


def test_missing_manager_member_has_index_and_original_cause():
    from django.core.exceptions import ObjectDoesNotExist

    class Member(GeneralManager):
        class Interface(CalculationInterface):
            id = Input(int)

        def __init__(self, **kwargs):
            if kwargs.get("id") == 99:
                raise ObjectDoesNotExist
            super().__init__(**kwargs)

    with pytest.raises(TypeError, match=r"values\[1\]") as error:
        summary(Input(list[Member]))(values=[Member(id=1), 99])
    assert isinstance(error.value.__cause__, ObjectDoesNotExist)


def test_manager_identification_is_snapshotted():
    class Member(GeneralManager):
        class Interface(CalculationInterface):
            id = Input(int)

    member = Member(id=1)
    instance = summary(Input(list[Member]))(values=[member])
    member.identification["id"] = 99
    assert instance.identification == {"values": [{"id": 1}]}


class PickleNestedMember(GeneralManager):
    class Interface(CalculationInterface):
        label = Input(str)
        values = Input(list[int], normalizer=lambda value: [*value, 9])


class PickleNestedParent(GeneralManager):
    class Interface(CalculationInterface):
        members = Input(list[PickleNestedMember])


class PickleNestedScalarParent(GeneralManager):
    class Interface(CalculationInterface):
        member = Input(PickleNestedMember)


@pytest.mark.parametrize(
    "parent, name",
    [(PickleNestedParent, "members"), (PickleNestedScalarParent, "member")],
)
def test_nested_normalized_collection_pickle_and_deepcopy(parent, name):
    import pickle
    from copy import deepcopy

    member = PickleNestedMember(label="x", values=[1])
    instance = parent(**{name: [member] if name == "members" else member})
    for restored in (deepcopy(instance), pickle.loads(pickle.dumps(instance))):  # noqa: S301
        assert restored.identification == instance.identification
        restored_member = restored.members[0] if name == "members" else restored.member
        assert restored_member.values == [1, 9]
