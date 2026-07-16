# Safe GraphQL Mutation Errors

Use `PublicGraphQLError` only for messages and codes that are intentionally part
of the client contract. Use Django `ValidationError` for input validation, and
let unexpected exceptions reach GeneralManager's mutation boundary so their
details are replaced with a correlated internal error.

## Define a mutation with explicit client failures

```python
from django.core.exceptions import ValidationError

from general_manager.api import PublicGraphQLError, graph_ql_mutation
from projects.managers import Project


@graph_ql_mutation
def archive_project(info, project_id: int, reason: str) -> Project:
    project = Project(id=project_id)
    if project.status == "archived":
        raise PublicGraphQLError(
            "The project is already archived.",
            code="PROJECT_ALREADY_ARCHIVED",
        )
    if not reason.strip():
        raise ValidationError({"reason": ["An archive reason is required."]})
    return project.update(
        status="archived",
        archive_reason=reason,
        creator_id=getattr(info.context.user, "id", None),
    )
```

The explicit public failure appears in GraphQL's top-level `errors` list:

```json
{
  "data": {"archiveProject": null},
  "errors": [
    {
      "message": "The project is already archived.",
      "extensions": {"code": "PROJECT_ALREADY_ARCHIVED"}
    }
  ]
}
```

A structured `ValidationError` uses code `BAD_USER_INPUT`, the message
`Validation failed.`, and schema-named `fieldErrors`/`nonFieldErrors` extension
entries. A `PermissionError` uses only `Permission denied.` and
`PERMISSION_DENIED`.

## Handle the stable client contract

```javascript
export class GraphQLRequestError extends Error {
  constructor(failures) {
    super("GraphQL request failed.");
    this.name = "GraphQLRequestError";
    this.failures = failures;
  }
}

export function mapGraphQLErrors(errors, {isPublicCode = () => false} = {}) {
  return errors.map((error) => {
    const extensions = error.extensions ?? {};
    const code = typeof extensions.code === "string"
      ? extensions.code
      : "GRAPHQL_ERROR";

    if (code === "BAD_USER_INPUT") {
      return {
        code,
        message: "Validation failed.",
        fieldErrors: extensions.fieldErrors ?? {},
        nonFieldErrors: extensions.nonFieldErrors ?? [],
      };
    }
    if (code === "INTERNAL_SERVER_ERROR") {
      return {
        code,
        message: "An internal server error occurred.",
        errorId: typeof extensions.errorId === "string"
          ? extensions.errorId
          : null,
      };
    }
    if (code === "PERMISSION_DENIED") {
      return {code, message: "Permission denied."};
    }
    if (isPublicCode(code)) {
      return {code, message: error.message};
    }
    return {code: "GRAPHQL_ERROR", message: "The request failed."};
  });
}

const result = await graphqlClient.mutate({
  mutation: ARCHIVE_PROJECT,
  variables: {projectId, reason},
});

const failures = mapGraphQLErrors(result.errors ?? [], {
  isPublicCode: (code) => code === "PROJECT_ALREADY_ARCHIVED",
});
for (const failure of failures) {
  switch (failure.code) {
    case "PROJECT_ALREADY_ARCHIVED":
      showNotice(failure.message);
      break;
    case "BAD_USER_INPUT":
      showFieldErrors(failure.fieldErrors);
      showNonFieldErrors(failure.nonFieldErrors);
      break;
    case "INTERNAL_SERVER_ERROR":
      if (failure.errorId) reportSupportId(failure.errorId);
      break;
    default:
      showGenericFailure();
  }
}
```

Do not display or branch on the text of an internal error. Its public message is
always `An internal server error occurred.`; `extensions.errorId` correlates the
response with server logs. Since GeneralManager 0.63.0, plain `ValueError` and
other unexpected exceptions are internal errors rather than `BAD_USER_INPUT`.
Keep `mapGraphQLErrors` in a shared client module so every GraphQL caller uses
the same allowlist for intentionally public codes.

See the [custom-mutation concept](../concepts/graphql/custom_mutations.md), the
[GraphQL task guide](../howto/expose_via_graphql.md), and the
[GraphQL API reference](../api/graphql.md).
