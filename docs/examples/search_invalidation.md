# Related search invalidation

Use a `SearchInvalidationRule` when a search document includes data owned by a
different manager. The rule belongs on the manager that owns the document. This
recipe updates projects when their customer changes and when an investment
number is added to or removed from the project's many-to-many relation.

## Declare the rules

Assume `Customer` and `InvestNumber` are existing ORM-backed managers:

```python
from general_manager import (
    GeneralManager,
    IndexConfig,
    SearchChange,
    SearchInvalidationRule,
)
from myapp.managers import Customer, InvestNumber


def projects_for_customer(
    change: SearchChange,
    owner_class: type[GeneralManager],
):
    # The source still exists during the before phase of an update or delete.
    return owner_class.filter(customer=change.instance)


def projects_for_investment(
    change: SearchChange,
    owner_class: type[GeneralManager],
):
    return owner_class.filter(invest_numbers=change.instance)


class Project(GeneralManager):
    class SearchConfig:
        indexes = (
            IndexConfig(
                name="global",
                fields=("name", "customer__name", "invest_numbers__name"),
                filters=("status",),
                sorts=("name",),
            ),
        )
        invalidation_rules = (
            SearchInvalidationRule(
                source=Customer,
                resolve=projects_for_customer,
                indexes=("global",),
            ),
            SearchInvalidationRule(
                source=InvestNumber,
                resolve=projects_for_investment,
                relation="invest_numbers",
                indexes=("global",),
            ),
        )
```

`SearchChange` is frozen and provides `action`, `phase`, `instance`, and
`database_alias`. Create resolves after the source exists, update resolves
before and after, and delete resolves before deletion. Each resolver must yield
`Project` manager instances; yielding identifiers, ORM model instances, or a
different manager type switches that rule to dirty-index reconciliation.

Use a dotted string such as
`"myapp.managers.Customer"` for `source` when it avoids an import cycle. Omit
`indexes` to select every configured project index. Set `resolve=None` when
individual targets cannot be discovered and the selected index pairs should
always be repaired by reconciliation.

The `relation` value is the owner-side many-to-many field. It enables exact
invalidation for related-manager `add()`, `remove()`, `clear()`, and `set()` in
both directions, including custom through models. The owner must use exactly
the standard `{"id": pk}` identification, and both through foreign keys must
target their endpoint primary keys. Direct through-model writes, raw SQL, bulk
writes, and self-symmetrical relations are unsupported.

## Bound and repair the work

Configure bounds for resolver results and task payloads:

```python
GENERAL_MANAGER = {
    **GENERAL_MANAGER,
    "SEARCH_INVALIDATION_MAX_TARGETS": 1000,
    "SEARCH_INVALIDATION_BATCH_SIZE": 100,
}
```

Both settings must be positive, non-boolean integers. Resolver errors, invalid
targets, and target overflow discard partial targeted work and leave the exact
owner/index pairs dirty. Run reconciliation after unsupported writes or when a
fallback needs repair:

```bash
python manage.py search_reconcile --once
```

Apply migration `0011_search_index_state_dirty_generation` before deploying
lifecycle invalidation workers. Search dispatch is commit-bound, but a
non-default source database still has a documented crash window between the
source commit and the control-plane callback; periodic reconciliation remains
the repair path.
