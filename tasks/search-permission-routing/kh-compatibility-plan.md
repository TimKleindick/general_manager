# KH compatibility follow-up

The user authorized implementing the remaining GM and KH changes after PR #583.
Related work: GM #304, closed #582, current #583 and KH #10933. Preserve the
existing dirty KH checkout; use an isolated candidate for integration checks.

## Ownership and sequence

1. Repair Python 3.14 search classification. Compiler annotation metadata must
   not count as executable permission policy; real custom policies stay unknown.
2. Extend the owning query-field authorizer with explicit list-only budget
   accounting. Proven local fields with no additional read rule do not consume
   the additional-field budget. Every runtime field and related-row check still
   runs against the full row-visible list domain, including nonmatching rows.
   Custom, unknown and delegated policies remain budgeted. Search and direct
   authorizer callers retain their existing default accounting.
3. Restore safe calculation constructor scoping from the earlier implementation,
   reviewed against the current code. Native scalar and manager-identity input
   constraints delimit the requested calculation domain. Freeze that domain,
   intersect permission constraints, then authorize operands before evaluating
   computed or related-value predicates. Preserve native input normalization,
   exact membership, atomic excludes and deny-all laziness.
4. Validate the existing KH dependency declarations and complete pagination
   changes in an isolated snapshot. Preserve all 1,000 bookings and the original
   full-year calendar request. No reduction of fixtures, permission checks or
   acceptance thresholds is an implementation fix.
5. Run targeted regressions, original KH gates, comparison harness and full GM
   and KH backend suites; then quality checks and the appropriate hosted CI.
   Update PR descriptions and the durable validation report from actual results.

The user explicitly chose restoring the 0.86.4 omission contract on 2026-11-09:
when both `page` and `pageSize` are omitted, local lists return complete results.
Explicit pagination retains finite defaults, configured limits and validation.
Unpaged remote queries require a complete upstream response. The original KH
20-day and 365-day tests remain unchanged and pass with this restoration.

## Boundaries

- `graphql_query_permissions.py` owns operand checks and their accounting.
  Existing permission helpers own local policy semantics; no parallel policy
  evaluator or permission-result cache is introduced.
- `graphql_resolvers.py` and `CalculationBucket` own constructor scoping and
  native predicate application. Client and permission constraints must intersect.
- Do not reuse a Permission instance across independent field checks: its native
  action cache would change callback execution and potentially denial behavior.
- Index freshness remains separate index maintenance, per user instruction.
- No manual GM version change, production mutation, merge or release is part of
  preparing these reviewable fixes. Published KH pins remain a release boundary.
- Full KH frontend checks require the exact private UI dependency; use hosted
  checks if it cannot be installed locally, never substitute another version.

## Acceptance

- Python 3.12 and 3.14 routing regressions; custom policies still choose thorough.
- Large list membership/count/order/pages unchanged; denied off-page and
  nonmatching operands still error; restrictive/unknown budget remains enforced.
- Runtime policy changes, relation target gates, aliases, partial remote sources
  and unchanged strict native-search budgets remain covered.
- Calculation input overlap, exclusions, normalization, exact-project domains,
  empty/denied scopes and computed-property denial regressions pass.
- Original KH year and 1,000-booking tests, separate search/calendar/project
  benchmarks, full relevant suites and CI are reported independently.
