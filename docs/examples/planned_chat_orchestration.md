# Chat discovery, query and client recipes

These recipes use the current Python dispatcher and native GraphQL read
contract. Run Python snippets inside an initialized Django application whose
managers are registered and exposed. Replace application-specific manager/field
names only after observing the actual schema. They do not authenticate an
anonymous Python caller or reproduce the complete HTTP admission layer.

## Discover an exposed manager

```python
from general_manager.chat.tools import execute_chat_tool

# In a Django view, request is the actual authenticated HttpRequest.
context = request
matches = execute_chat_tool("search_managers", {"query": "part"}, context)
print(matches)
```

Pick the exact manager name from the result, not a guessed GraphQL type name.
For the remaining examples assume the application's registered exposed manager
is `Part`. If discovery returns a different name, use that name instead.

## Load exact schema details

```python
overview = execute_chat_tool("get_manager_schema", {"manager": "Part"}, context)
if overview is None or overview.get("status") == "error":
    raise RuntimeError("Part is unavailable in this chat contract")

print(overview["roots"])
print(overview["output_fields"])
print(overview["type_manifest"])

# First observe the owning output definition. Follow input/enum references
# from the overview or subsequent detail manifests only when the query needs them.
selected_types = [overview["type"]]
details = execute_chat_tool("get_manager_schema", {
    "manager": "Part",
    "view": "detail",
    "types": selected_types,
    "snapshot": overview["snapshot"],
}, context)
if details.get("status") == "error":
    raise RuntimeError(details["code"])
assert set(details["types"]) == set(selected_types)
```

To observe a filter input or enum, choose its exact `kind`/name from the current
manifest and request it using the same manager snapshot. Further manifests can
advertise nested definitions. An empty `types` list is invalid. A related manager
reference requires its own overview. Refresh the overview if a schema rebuild
invalidates the snapshot; never substitute a full schema as an implicit recovery.

This sequence observes definitions. In the planned scheduler it establishes a
requirement only when correctly linked to that task's declared scope.

## Query an observed root

For an application whose observed `Part` output contains `code` and `name`:

```python
assert {"code", "name"} <= set(overview["output_fields"])
if len(overview["roots"]) != 1:
    raise RuntimeError("Choose the intended advertised root explicitly")

page = execute_chat_tool("query", {
    "manager": "Part",
    "root": overview["roots"][0],
    "fields": ["code", "name"],
    "limit": 20,
    "offset": 0,
}, context)
print(page["data"])
print(page["total_count"], page["has_more"], page["complete"])
```

This is a bounded page, not a claim to list every part. Before an exhaustive
answer, account for all root and selected nested pages. A field assertion here
catches an incompatible example application early; it does not grant field access.

The direct Python helper has a required `filters` keyword even when no filter
is wanted:

```python
from general_manager.chat.tools import query

page = query(
    manager="Part",
    root=overview["roots"][0],
    filters={},
    fields=["code", "name"],
    limit=20,
    context=request,
)
```

If the observed root filter input contains `name`, `filters={"name": "Bolt"}`
is its direct shape. The explicit equivalent is `arguments={"filter": {"name":
"Bolt"}}` only if the actual argument is named `filter`. Use one form at a time.
Nested relations use the exposed input types and native names; do not add an
extra `filter` wrapper to the convenience `filters` mapping.

## Select a relation page

Suppose discovery advertises `Project.materialsList`, its `pageSize` argument,
`items.code`, `items.name` and `pageInfo.totalCount`. The exact selection is:

```json
{
  "manager": "Project",
  "filters": {},
  "fields": [
    "code",
    {
      "field": "materialsList",
      "arguments": {"pageSize": 10},
      "fields": [
        {"items": ["code", "name"]},
        {"pageInfo": ["totalCount"]}
      ]
    }
  ]
}
```

This is a conditional application contract, not a built-in universal manager.
Use actual wrapper and argument names. The root adapter adds its own page
wrapper, whereas nested collection paths remain explicit. A root `has_more=false`
does not prove all nested materials were returned.

## Declare quantity/factor metadata

Applications can declare public dimensions on their existing interface:

```python
from general_manager.interface.unit_contract import FieldUnitContract

# Install these entries on the existing Interface.field_unit_contracts mapping
# only when the named numeric/string attributes actually exist.
field_unit_contracts = {
    "quantity": FieldUnitContract.quantity(
        unit_field="unit", units={"widgets": "count"},
    ),
    "mass_per_piece": FieldUnitContract.factor(
        source_unit="count", target_unit="g",
    ),
}
```

Rebuild the executable schema after changing declarations. This metadata states
dimensions; it does not supply values, identities or a conversion query.
Complete conversion still needs current task-linked schema witnesses, selected
quantity/unit/factor/identity paths and the supported complete population. See
[evidence and calculations](../concepts/chat_evidence.md).

## Inspect a stream

With a valid Django session and CSRF token:

```bash
curl -N -X POST http://localhost:8000/chat/stream/   -H 'Content-Type: application/json'   -H 'Accept: text/event-stream'   -H 'Cookie: sessionid=<session-cookie>; csrftoken=<csrf-token>'   -H 'X-CSRFToken: <csrf-token>'   --data '{"text":"List the available materials"}'
```

Use the actual configured chat base URL. The placeholders must come from the
application's normal session/CSRF flow; they are not credentials to copy from
another user's environment. A representative terminal looks like:

```json
{
  "type": "done",
  "usage": {"input_tokens": 120, "output_tokens": 24},
  "orchestration": {
    "status": "complete",
    "coverage": {"resolved": 1, "total": 1},
    "unresolved": []
  }
}
```

Read actual coverage instead of assuming every turn resolves one task. For
partial answers and confirmation UI, continue with
[client integration](../howto/integrate_chat.md).
