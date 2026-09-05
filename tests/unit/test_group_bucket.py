from __future__ import annotations

import pytest

from general_manager.bucket.base_bucket import Bucket
from general_manager.bucket.group_bucket import GroupBucket
from general_manager.api.graphql_groups import (
    GroupQueryError,
    create_group_resolver,
)


class GroupBucketManager:
    class Interface:
        @staticmethod
        def get_attributes() -> dict[str, object]:
            return {"category": {}}

        @staticmethod
        def get_attribute_types() -> dict[str, dict[str, object]]:
            return {"category": {"type": str}}


class GroupBucketList(list[GroupBucketManager]):
    def filter(self, **kwargs: object) -> "GroupBucketList":
        return GroupBucketList(
            [
                manager
                for manager in self
                if all(getattr(manager, key) == value for key, value in kwargs.items())
            ]
        )

    def exclude(self, **kwargs: object) -> "GroupBucketList":
        return GroupBucketList(self)

    def __or__(self, other: object) -> "GroupBucketList":
        return GroupBucketList([*self, *list(other)])  # type: ignore[arg-type]

    def none(self) -> "GroupBucketList":
        return GroupBucketList()


def test_empty_slice_is_an_empty_explicit_group_bucket() -> None:
    """Empty group pages must keep their group result shape for pagination."""
    member = GroupBucketManager()
    member.category = "first"
    grouped = GroupBucket(GroupBucketManager, ("category",), GroupBucketList([member]))

    empty_page = grouped[1:2]

    assert isinstance(empty_page, GroupBucket)
    assert list(empty_page) == []
    assert not isinstance(grouped, Bucket)


def test_collection_key_is_rejected_before_grouping_an_empty_graphql_source() -> None:
    """Generated GraphQL grouping rejects wrapped collection keys before reads."""

    class CollectionManager:
        class Interface:
            @staticmethod
            def get_attribute_types() -> dict[str, dict[str, object]]:
                return {
                    "name": {"type": str},
                    "members": {
                        "type": GroupBucketManager,
                        "relation_kind": "collection",
                    },
                    "tags": {"type": list[str]},
                    "optional_tags": {"type": list[str] | None},
                }

    source_reads = 0

    def get_empty_source(_root: object, _include_inactive: bool) -> None:
        nonlocal source_reads
        source_reads += 1
        return None

    resolver = create_group_resolver(get_empty_source, CollectionManager)
    for field_name in ("members", "tags", "optionalTags"):
        with pytest.raises(GroupQueryError, match="not an eligible grouping key"):
            resolver(None, None, group_by=[field_name])

    assert source_reads == 0
