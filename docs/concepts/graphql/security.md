# Security

Security in the GraphQL layer relies on permission checks and robust error handling.

## Permission enforcement

- Every query obtains read filters from the manager's `Permission` class via `getReadPermissionFilter()`.
- Mutations invoke `checkCreatePermission`, `checkUpdatePermission`, or `checkDeletePermission` before executing. Permission errors translate into `success: false` responses with descriptive messages.
- Attribute-level restrictions hide protected fields even when the user can access the object.

Always execute GraphQL resolvers through managers; do not reach directly for Django models, or you will bypass permission rules.

## Authentication

Set `AUTHENTICATION_BACKENDS` and middleware according to your project. The GraphQL view expects `info.context.user` to be populated. Denied permissions return a GraphQL error or an error entry in the mutation payload.

## Error propagation

Validation errors from interfaces and rules bubble up as GraphQL `GraphQLError` instances. Use try/except blocks in custom resolvers to add more context while preserving the original message for clients.

## Hardening tips

- Enable query depth or complexity limits in your GraphQL server to avoid expensive queries.
- Combine permissions with `filter` arguments so users cannot guess identifiers of objects they do not own.
- Log denied permissions with the manager name and user ID to monitor suspicious behaviour.
- Avoid exposing `ignore_permission=True` paths in public APIs; reserve them for internal management commands.
