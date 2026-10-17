# Positive selection roles and validation-cycle state (R57)

The saved R56 E072 Judge request describes both `constraints.all_materials` and
`constraints.excluded_materials` as sources of `material_ids`. The oracle instead
requires only the positively selected materials there. Reference **1.13** fixes
that contradictory extraction instruction through optional, value-free
`selection_target_paths` on the existing entity-field descriptor. Its declared
paths must exist with the same manager and shape; malformed explicit metadata
raises an error without falling back to same-manager inference. Fields without
explicit metadata retain their previous guidance.

Positive targets and exclusions remain independently required. A project must
contain Brass and no Copper, including mixtures; other unexcluded materials are
allowed. An answer actually including Copper as a positive target remains
representable and fails. No parser subtraction, Gold-derived filling, score
exception or expected-ID enum is introduced. All 1.12 facts, arithmetic, source
requirements and semantic checks are inherited, including optional cost details
and the stricter explicitly requested WBS breakdowns.

Catalog/report/oracle/harness Python entry points and ordinary CLI runs now
default to reference 1.13. Archival callers must explicitly select 1.10, 1.11 or
1.12. All 149 contracts and blind shapes for each remain byte-exact by canonical
hash. Requests, parsed extractions and scoring bind the selected reference;
1.13 scores carry `evaluation_revision="measurement-contracts-7"`. Saved requests
continue to be parsed against their own saved schema. The envelope remains 1.5,
and frozen catalog bytes, source seeds and historical results are unchanged.

For E078, both final `bind_calculation` outputs are malformed JSON, including
after validation feedback. They remain rejected. The runtime excludes the
top-level operational fields `action_validation_error`, `manager_candidates`
and `last_tool_round` from its material-state signature; the old external syntax
certificate omitted the third exclusion and could not match the runtime audit
digest. The scheduler now exposes its existing projection through
`execution_state_signature`; runtime behavior and encoding are unchanged.
`experiments.gm_eval.validation_cycles.validation_cycle_state_matches` reuses it
to verify equal material states and the exact audit hash. All nested and unknown
fields remain material; neither helper mutates its input.

This adapter proves only the state subpredicate. An operational certificate must
still independently validate the original turn, raw action and feedback, pass
ordering, complete history, source and fixture bindings, request chronology,
HTTP/SSE lifecycle, auth/quota/transport integrity and cleanup. An unproved
terminal still globally stops. It grants no POST retry or automatic continuation.
Migrate a certificate only in a new source-bound namespace, replacing its
duplicated state projection with this adapter; retain old frozen certificates.

Regression fixtures retain the original synthetic E072 queries and reduced E078
reference/action pair with source-report hashes. They are offline protocol
controls, not new model-quality measurements or edits to past scores. The complete
offline replay still rejects an original `response.metadata` stream event not
supported by the frozen lifecycle certificate. A matching state hash alone
therefore does not prove that terminal; the global STOP remains. This change
does not skip or invent support for that event. No new API call or rerun is part
of this change.
