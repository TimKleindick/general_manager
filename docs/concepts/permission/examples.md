# Practical Permission Examples

This page collects small, decision-oriented examples for the permission system. Use it when you already understand the permission primitives and want to see which pattern fits a real policy.

## Example 1: broad default with one tighter field

Use `AdditiveManagerPermission` when most fields share a broad rule and one field needs an extra gate.

```python
from general_manager.manager import GeneralManager
from general_manager.permission.manager_based_permission import (
    AdditiveManagerPermission,
)


class Project(GeneralManager):
    name: str
    budget: int

    class Permission(AdditiveManagerPermission):
        __read__ = ["isAuthenticated"]
        __update__ = ["isProjectMember"]

        budget = {
            "read": ["isFinanceTeam"],
            "update": ["isFinanceTeam"],
        }
```

Result:

- most fields require `isProjectMember` for updates
- `budget` updates require both `isProjectMember` and `isFinanceTeam`
- `budget` reads require both `isAuthenticated` and `isFinanceTeam`

Choose this when the field-specific rule is an extra restriction, not an exception to the base rule.

## Example 2: broad default with one replacement field

Use `OverrideManagerPermission` when one field should follow its own rule instead of inheriting the class-level CRUD rule.

```python
from general_manager.manager import GeneralManager
from general_manager.permission.manager_based_permission import (
    OverrideManagerPermission,
)


class Contract(GeneralManager):
    title: str
    internal_notes: str

    class Permission(OverrideManagerPermission):
        __read__ = ["isAccountManager"]

        internal_notes = {
            "read": ["isLegalTeam"],
        }
```

Result:

- most fields require `isAccountManager`
- `internal_notes` requires only `isLegalTeam`
- the base `__read__` rule still applies to every other field

Choose this when a field-specific rule is a replacement policy, not an additional gate.

## Example 3: delegated object gate with local field rules

Use `__based_on__` when access to a nested object must never bypass the related object's permission outcome.

```python
from general_manager.manager import GeneralManager
from general_manager.permission.manager_based_permission import (
    OverrideManagerPermission,
)


class InvoiceAttachment(GeneralManager):
    invoice: "Invoice"
    file_name: str
    audit_note: str

    class Permission(OverrideManagerPermission):
        __based_on__ = "invoice"
        __read__ = ["isAuthenticated"]

        audit_note = {
            "read": ["isFinanceLead"],
        }
```

Result:

- the linked `invoice` permission is checked first
- if the invoice denies access, the attachment denies access
- if the invoice allows access, `audit_note` then uses the local override rule

`__based_on__` is always an outer gate for both additive and override permission classes.

## Example 4: queryset filtering for list endpoints

Permission filters are most useful when a read rule can be expressed as a queryset constraint.

```python
from general_manager.permission import register_permission


@register_permission(
    "belongsToCustomer",
    permission_filter=lambda user, config: {
        "filter": {"customer_id": user.customer_id}
    },
)
def belongs_to_customer(instance, user, config):
    return instance.customer_id == user.customer_id
```

Then use it in a permission class:

```python
class OrderPermission(AdditiveManagerPermission):
    __read__ = ["belongsToCustomer"]
```

Result:

- bucket/list queries can be narrowed before records are materialised
- row visibility is still enforced per instance before results and counts are returned
- the same rule stays reusable across GraphQL and manager-level access
- GraphQL emits one aggregate structured log summary for the read path, so observability tooling can see when the final instance gate did the real work

## Example 5: focused permission tests

Permission tests should exercise both granted and denied paths directly through the helper API.

```python
import pytest

from general_manager.permission.base_permission import PermissionCheckError


def test_finance_user_can_update_budget(project_instance, finance_user):
    payload = {"budget": 10_000}
    Project.Permission.check_update_permission(
        payload,
        project_instance,
        request_user=finance_user,
    )


def test_non_finance_user_cannot_update_budget(project_instance, project_member):
    payload = {"budget": 10_000}
    with pytest.raises(PermissionCheckError):
        Project.Permission.check_update_permission(
            payload,
            project_instance,
            request_user=project_member,
        )
```

For list behavior, add a separate test that inspects `get_permission_filter()` or hits the GraphQL/list endpoint that consumes it.
