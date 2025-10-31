# Permission Cookbook

These recipes provide drop-in patterns for the permission system. They highlight how `ManagerBasedPermission` composes with reusable checks, attribute overrides, and queryset filters.

## Attribute-level rule sets

```python
from general_manager.permission.manager_based_permission import ManagerBasedPermission


class InvoicePermission(ManagerBasedPermission):
    __read__ = ["isAuthenticated"]
    __create__ = ["inGroup:finance"]
    __update__ = ["inGroup:finance"]
    __delete__ = ["isAdmin"]

    total_due = {"update": ["matches:status:open"]}
    paid_at = {"read": ["inGroup:finance"], "update": ["inGroup:finance"]}
```

- Restrict write access to finance operators.
- Allow anyone to read invoices but hide `paid_at` for unauthorised users.
- `matches` uses the helper registered in the [`permission_checks` registry](../api/permission.md#registry-and-reusable-checks) to guard updates based on the current field value.

## Delegating through `__based_on__`

```python
class InvoiceAttachmentPermission(ManagerBasedPermission):
    __based_on__ = "invoice"
    __read__ = ["isAuthenticated"]
    __create__ = ["inGroup:finance"]
    file = {"update": ["inGroup:finance"], "delete": ["inGroup:finance"]}
```

Attachments inherit the invoice's permission outcome. If the linked invoice denies access, the attachment is denied as well. Filters from the invoice permission are automatically prefixed with `invoice__` when applied to queries.

## Combining custom checks with filters

```python
from general_manager.permission.permission_checks import register_permission


@register_permission(
    "belongsToOrganisation",
    permission_filter=lambda user, config: {
        "filter": {f"{config[0]}__organisation_id": user.organisation_id}
    }
    if config
    else None,
)
def permission_belongs_to_org(instance, user, config):
    relation = getattr(instance, config[0])
    return relation.organisation_id == user.organisation_id
```

Use the permission by adding `"belongsToOrganisation:customer"` to `__read__`. The filter keeps queryset results inside the user's organisation without duplicating logic.

## Guarding GraphQL mutations

Mutation classes can reuse permission checks for fine-grained control. The example below assumes an `Invoice` manager backed by a Django model:

```python
from django.db.models import AutoField, CharField, TextField
from general_manager.interface.database_interface import DatabaseInterface
from general_manager.manager import GeneralManager


class Invoice(GeneralManager):
    id: int
    status: str
    rejection_reason: str | None

    class Interface(DatabaseInterface):
        id = AutoField(primary_key=True)
        status = CharField(max_length=32)
        rejection_reason = TextField(null=True, blank=True)

    class Permission(InvoicePermission):
        ...
```

Because the GraphQL decorator emits `ID` inputs for manager arguments, the resolver and the accompanying mutation permission receive the identifier and must instantiate the manager explicitly:

```python
from typing import Any

from general_manager.api.mutation import graph_ql_mutation
from general_manager.permission.mutation_permission import MutationPermission


class RejectInvoicePermission(MutationPermission):
    @classmethod
    def check(cls, data: dict[str, Any], request_user: Any) -> None:
        invoice_id = int(data["invoice"])
        invoice = Invoice(id=invoice_id)
        if invoice.status != "submitted":
            cls.raise_error("Only submitted invoices can be rejected.")
        if not request_user.groups.filter(name="finance_lead").exists():
            cls.raise_error("Only finance leads may reject invoices.")


@graph_ql_mutation(permission=RejectInvoicePermission)
def reject_invoice(info, invoice: Invoice, reason: str) -> Invoice:
    invoice_id = int(invoice)
    manager = Invoice(id=invoice_id)
    updated = manager.update(
        creator_id=getattr(info.context.user, "id", None),
        status="rejected",
        rejection_reason=reason,
    )
    return updated
```

- `graph_ql_mutation` inspects the resolver signature and return annotation to build the GraphQL payload; no separate `base_type` configuration is required.
- `MutationPermission.check` is a classmethod that receives the mutation data and `request_user`, so convert IDs into managers before enforcing domain rules.
- Use `raise_error()` to produce a structured GraphQL error with `success=False`.
- Call `GeneralManager.update` instead of writing to model fields directly; it re-runs permission checks and records history comments when provided.

## Testing shortcuts

```python
from general_manager.permission.base_permission import BasePermission, PermissionCheckError


def test_finance_cannot_delete_archived_invoice(finance_user, archived_invoice):
    with pytest.raises(PermissionCheckError):
        BasePermission.check_delete_permission(
            archived_invoice,
            request_user=finance_user,
        )
```

Combine permission helper calls with fixtures to cover both granted and denied scenarios. Stubbing the audit logger makes it easy to assert on emitted `PermissionAuditEvent` instances.
