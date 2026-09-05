# Return a Persisted Item Order Through GraphQL

Use a calculation manager when an application needs to expose an arbitrary,
persisted item order without changing GeneralManager's generated list
resolvers. This recipe stores a user's preferred item IDs, returns the valid
preferred items first, and appends every remaining item in a deterministic
fallback order.

The example uses a Django authentication user as the owner, an `Item` manager,
and one sorting category named `example_category`.

## Store the preferred IDs

Keep the preference record in the application that owns the behavior. The IDs
are stored as a JSON list because their sequence is significant.

```python title="myapp/models.py"
from django.conf import settings
from django.db import models


class UserItemSorting(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="item_sortings",
    )
    sorting_category = models.CharField(max_length=80)
    item_ids = models.JSONField(default=list)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("owner", "sorting_category"),
                name="unique_user_item_sorting",
            ),
        ]
```

Create and apply a normal Django migration after adding this model. Treat
`item_ids` as an ordered preference, not as the source of truth for which items
exist. Items can be deleted after their IDs were stored.

## Register an ownership permission

The calculation accepts a user ID, so authentication alone is insufficient: an
authenticated caller must not be able to substitute another user's ID. Register
a rule that compares the requested `User` manager with the authenticated Django
user.

```python title="myapp/permissions.py"
from general_manager.permission import register_permission


@register_permission("ownsRequestedUser")
def owns_requested_user(instance, request_user, _config):
    requested_user = getattr(instance, "user", None)
    return bool(
        getattr(request_user, "is_authenticated", False)
        and requested_user is not None
        and requested_user.id == request_user.id
    )
```

Load the registration when the Django application starts:

```python title="myapp/apps.py"
from django.apps import AppConfig


class MyAppConfig(AppConfig):
    name = "myapp"

    def ready(self) -> None:
        from . import permissions  # noqa: F401
```

The rule intentionally has no queryset permission filter. It authorizes one
calculation instance whose `user` input is already known.

## Build the typed calculation

This example wraps Django's authentication user with `ExistingModelInterface`
and lets `DatabaseInterface` create the `Item` model. If the application already
has `User` and `Item` managers, reuse them and copy only `OrderedItems`.

```python title="myapp/managers.py"
from __future__ import annotations

import math

from django.conf import settings
from django.db import models

from general_manager.interface import (
    CalculationInterface,
    DatabaseInterface,
    ExistingModelInterface,
)
from general_manager.manager import GeneralManager, Input, graph_ql_property
from general_manager.permission import AdditiveManagerPermission

from .models import UserItemSorting


class User(GeneralManager):
    id: int
    username: str

    class Interface(ExistingModelInterface):
        model = settings.AUTH_USER_MODEL


class Item(GeneralManager):
    id: int
    name: str

    class Interface(DatabaseInterface):
        name = models.CharField(max_length=120)

    class Permission(AdditiveManagerPermission):
        __read__ = ["isAuthenticated"]


class OrderedItems(GeneralManager):
    user: User
    sorting_category: str

    class Interface(CalculationInterface):
        user = Input(User)
        sorting_category = Input(
            str,
            possible_values=("example_category",),
        )

    class Permission(AdditiveManagerPermission):
        __read__ = ["ownsRequestedUser"]

    @graph_ql_property
    def items(self) -> list[Item]:
        stored_ids = (
            UserItemSorting.objects.filter(
                owner_id=self.user.id,
                sorting_category=self.sorting_category,
            )
            .values_list("item_ids", flat=True)
            .first()
            or []
        )

        fallback_items = list(Item.all().sort("name", "id"))
        items_by_id = {item.id: item for item in fallback_items}

        preferred_items: list[Item] = []
        seen_ids: set[int] = set()
        for raw_id in stored_ids:
            if isinstance(raw_id, bool):
                continue
            if isinstance(raw_id, float) and (
                not math.isfinite(raw_id) or not raw_id.is_integer()
            ):
                continue
            try:
                item_id = int(raw_id)
            except (TypeError, ValueError, OverflowError):
                continue
            if item_id in seen_ids:
                continue

            seen_ids.add(item_id)
            item = items_by_id.get(item_id)
            if item is not None:
                preferred_items.append(item)

        remaining_items = [
            item for item in fallback_items if item.id not in seen_ids
        ]
        return preferred_items + remaining_items
```

The algorithm does not rely on the database preserving the order of an `IN`
query:

1. `Item.all().sort("name", "id")` loads every eligible item in a stable
   fallback order. The ID breaks ties between equal names.
2. `items_by_id` makes each preferred-ID lookup independent of database result
   order.
3. `seen_ids` keeps only the first occurrence of a duplicate stored ID.
4. A stored ID absent from `items_by_id` is stale and is skipped safely. Values
   that cannot represent an integer ID, including booleans and non-integral or
   non-finite floats, are skipped as malformed data.
5. Items not selected by the stored sequence are appended in the original
   `(name, id)` fallback order.

The calculation returns all items here. Apply the application's ordinary item
visibility rules before constructing `fallback_items` if only a subset is
eligible. The ownership rule on `OrderedItems` must not be used to broaden the
read permissions on `Item` itself.

## Why `list[Item]` becomes a typed GraphQL list

GeneralManager inspects the return annotation on every `@graph_ql_property`.
For `items(self) -> list[Item]`, it recognizes the collection wrapper, resolves
`Item` to the registered generated GraphQL object type, and exposes `items` as
a GraphQL list of that type. Clients can therefore select nested `Item` fields
such as `id` and `name`; no manual Graphene field or list resolver is needed.

Keep the concrete `list[Item]` annotation. Replacing it with an untyped `list`
or a scalar annotation removes the information schema generation needs.

## Query the ordered items

GeneralManager generates a detail-style calculation field from the class name
and converts Python argument names to GraphQL camel case:

```graphql
query OrderedItems($userId: ID!) {
  orderedItems(
    userId: $userId
    sortingCategory: "example_category"
  ) {
    items {
      id
      name
    }
  }
}
```

With stored IDs for "Gamma" followed by "Alpha", and "Beta" not stored, a
representative response is:

```json
{
  "data": {
    "orderedItems": {
      "items": [
        {"id": "3", "name": "Gamma"},
        {"id": "1", "name": "Alpha"},
        {"id": "2", "name": "Beta"}
      ]
    }
  }
}
```

The `ownsRequestedUser` rule is evaluated when GraphQL resolves the calculation
fields. If the authenticated caller's ID differs from `userId`, `items` is not
resolved and its nullable GraphQL value is `null`. Applications that prefer a
top-level GraphQL error can add that policy at their GraphQL transport boundary,
but must keep the same server-side ownership check.

## Test ordering and permissions

The following pytest-django tests exercise the calculation directly for exact
ordering and use the GraphQL endpoint for the caller boundary. They assume the
application exposes GeneralManager at `/graphql/`.

```python title="myapp/tests/test_ordered_items.py"
import json

import pytest
from django.contrib.auth import get_user_model

from myapp.managers import Item, OrderedItems, User
from myapp.models import UserItemSorting


ORDERED_ITEMS_QUERY = """
query OrderedItems($userId: ID!) {
  orderedItems(
    userId: $userId
    sortingCategory: "example_category"
  ) {
    items { id name }
  }
}
"""


@pytest.fixture
def owner(db):
    return get_user_model().objects.create_user(
        username="list-owner",
        password="test-password",
    )


@pytest.fixture
def other_user(db):
    return get_user_model().objects.create_user(
        username="other-user",
        password="test-password",
    )


@pytest.fixture
def items(owner):
    return {
        name: Item.create(name=name, creator_id=owner.id)
        for name in ("Gamma", "Alpha", "Beta")
    }


def ordered_names(owner):
    calculation = OrderedItems(
        user=User.get(id=owner.id),
        sorting_category="example_category",
    )
    return [item.name for item in calculation.items]


def save_order(owner, item_ids):
    UserItemSorting.objects.create(
        owner=owner,
        sorting_category="example_category",
        item_ids=item_ids,
    )


def test_preferred_items_keep_their_stored_order(owner, items):
    save_order(owner, [items["Gamma"].id, items["Alpha"].id])

    assert ordered_names(owner) == ["Gamma", "Alpha", "Beta"]


def test_remaining_items_use_deterministic_fallback_order(owner, items):
    save_order(owner, [items["Gamma"].id])

    assert ordered_names(owner) == ["Gamma", "Alpha", "Beta"]


def test_duplicate_ids_are_returned_once(owner, items):
    save_order(
        owner,
        [items["Beta"].id, items["Beta"].id, items["Alpha"].id],
    )

    assert ordered_names(owner) == ["Beta", "Alpha", "Gamma"]


def test_stale_ids_are_ignored(owner, items):
    stale_id = max(item.id for item in items.values()) + 1_000
    save_order(owner, [stale_id, items["Beta"].id])

    assert ordered_names(owner) == ["Beta", "Alpha", "Gamma"]


def test_boolean_ids_are_ignored(owner, items):
    save_order(owner, [True])

    assert ordered_names(owner) == ["Alpha", "Beta", "Gamma"]


def test_fractional_float_ids_are_ignored(owner, items):
    save_order(owner, [items["Gamma"].id + 0.5])

    assert ordered_names(owner) == ["Alpha", "Beta", "Gamma"]


@pytest.mark.parametrize(
    "non_finite_id",
    [float("nan"), float("inf"), float("-inf")],
)
def test_non_finite_float_ids_are_ignored(owner, items, non_finite_id):
    save_order(owner, [non_finite_id])

    assert ordered_names(owner) == ["Alpha", "Beta", "Gamma"]


def test_missing_preference_record_uses_fallback_order(owner, items):
    assert ordered_names(owner) == ["Alpha", "Beta", "Gamma"]


def test_owner_can_read_the_order_through_graphql(client, owner, items):
    save_order(owner, [items["Gamma"].id])
    client.force_login(owner)

    response = client.post(
        "/graphql/",
        data=json.dumps(
            {
                "query": ORDERED_ITEMS_QUERY,
                "variables": {"userId": str(owner.id)},
            }
        ),
        content_type="application/json",
    )

    payload = response.json()
    assert "errors" not in payload
    assert [item["name"] for item in payload["data"]["orderedItems"]["items"]] == [
        "Gamma",
        "Alpha",
        "Beta",
    ]


def test_caller_cannot_read_another_users_order(
    client,
    owner,
    other_user,
    items,
):
    save_order(owner, [items["Gamma"].id])
    client.force_login(other_user)

    response = client.post(
        "/graphql/",
        data=json.dumps(
            {
                "query": ORDERED_ITEMS_QUERY,
                "variables": {"userId": str(owner.id)},
            }
        ),
        content_type="application/json",
    )

    payload = response.json()
    assert payload["data"]["orderedItems"]["items"] is None
```

These tests keep separate assertions for preferred ordering, fallback ordering,
duplicates, stale and malformed IDs, and a missing record. That separation
makes a regression in one branch of the algorithm easy to identify. The two
GraphQL tests verify both sides of the ownership rule rather than relying only
on direct resolver tests.

## When to use this pattern

Prefer this calculation-property pattern when the order is application-specific,
persisted per user or owner, and represented by a relatively small sequence of
preferred IDs. It keeps generated list resolvers generic while providing one
server-tested order to every GraphQL client.

Compare it with the main alternatives:

### Sort the complete result in the frontend

Frontend sorting can be reasonable when all items are already loaded, the order
is temporary presentation state, and only one client needs it. It is a poor fit
for persisted shared behavior: every client must reimplement stale-ID and
duplicate handling, and each client must download the complete result before it
can establish the final order.

### Extend a generated list query with ordering arguments

Additional list arguments are useful for broadly reusable field-based ordering,
such as `name` ascending or `created_at` descending, especially when the
database can sort before pagination. An arbitrary persisted ID sequence is not
a normal sort key. Teaching a generated resolver about an application's
preference table couples domain policy to schema-generation internals and makes
the resolver harder to upgrade.

### Change GeneralManager itself

Change the package only when the behavior is generic enough to benefit many
independent applications and can be expressed without importing an
application-owned model. A user-specific preference record and category are
application concerns. Keeping them in a calculation manager is a smaller,
upgrade-safe change with focused permission and ordering tests.
