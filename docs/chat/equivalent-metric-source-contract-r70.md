# Equivalent metric source routes

The default experiment reference is **1.17**. Explicit 1.10–1.16 selectors retain
all 149 archived expectations and blind extraction shapes. Catalog bytes, Gold
arithmetic and historical scores are unchanged. Reassessment under 1.17 must be a
separately identified artifact; adopting the default never rewrites an old run.

A source requirement may now declare `equivalent_routes` in the evaluator's
trusted oracle. This is a reviewed same-metric input contract, not permission to
accept any manager that happens to produce the expected number. The current
planned net revenue route accepts Project's declared plan year, quantity, net
price and currency inputs. Null quantities/prices remain unknown. Foreign
currency conversion additionally requires selected `fxRate`, `fxAsOf` and
`fxSource` matching the unique independent base-currency/EUR-quote reference
used by the numeric oracle. EUR rows do not acquire unnecessary FX-field
requirements. Neither matching amounts nor a claimed rate establish conversion
provenance; missing or mismatched reference metadata cannot qualify. A foreign
plan with null quantity/price stays unknown without fabricating a conversion.

`oracle.source` owns route declarations and question-specific population. A
single-project metric needs that project's complete eligible inputs; ranking
and zero/missing separation need the entire eligible population. The scorer's
existing source and internal-grounding owners verify successful, selected,
complete root query windows and exact population/input values. Pagination uses
the existing completeness verifier. Undeclared routes, missing/extra rows,
unselected fields, changed input metrics/currency/year and invented values
cannot qualify. Expected input rows stay evaluator-only and are absent from the
blind judgment packet.

Eligible input data alone does not establish correct use. Answer-quote fact
support and independent `answer_supported` evidence must bind the actually used
records; all result, metric, units, arithmetic, population, unknowns and residual
semantic checks still apply. Generic source/citation requests may cite the
actual equivalent route. Physical provenance contracts retain their required
interface facts. An explicit source restriction is trusted reference metadata:
`source_restrictions` maps a source role to allowed manager names, for example
`{"planned_net_revenue": ["ProjectCommercial"]}`. It restricts both canonical
and equivalent routes; the scorer does not infer an exemption from answer prose,
case identifiers or product-specific manager names.

Public Python defaults imported from `catalog.REFERENCE_VERSION` and the module
CLI now select 1.17. Callers reproducing an archival run must pass its original
`reference_version` explicitly. New judge requests, extractions and scores bind
the selected reference, inheriting the 1.16 runtime-origin and request-integrity
checks. This change creates no model call, retry or new performance claim.
