# Single winners and explanatory comparisons

Evaluation reference 1.15 clarifies the result boundary for E099's two resolved
single-winner questions. The selected winner is separate from projects used to
explain that selection. A clear winner sentence can be accompanied by correct
comparison prose or a comparison table without turning every comparator into
another winner.

The oracle declares `result_claim_scope: single_winner_with_comparisons` for
those two turns. The existing blind extraction owner publishes this meaning in
`result_ids`, `ranked_ids` and `values`; the request hash binds those descriptions,
the response schema and reference version. The system instruction follows the
same bound descriptions. It publishes no expected winner, value, ID or answer
count inferred from numeric gold. Other questions retain their existing shapes.

The extraction must preserve a wrong winner, wrong value, missing winner,
multiple asserted winners and contradictory winner claims. It must not infer an
unstated winner from table order or choose a correct answer from the evidence.
The independent scorer retains the exact original result, rank and value checks.

The existing required `answer_supported` check owns **all** volunteered claims.
It checks every comparison, table cell and ordering claim, including project and
material identities, quantities, units, periods, status restrictions, gross/net
and returns treatment, population counts and completeness claims. A correct
headline cannot excuse an incorrect comparison or a contradictory table. A
partial comparison need not list every candidate; a comparison explicitly
claimed complete must actually be complete. No second answer parser or scorer
heuristic duplicates this semantic responsibility.

## Version selection and saved answers

The shared `catalog.REFERENCE_VERSION` default advances to 1.15. Python entry
points importing that default, including catalog loading/validation, oracle
construction and harness runs, therefore select 1.15. The CLI's catalog output
uses the same default. Explicit Python selectors for 1.10 through 1.14 remain
supported and retain their exact contracts and extraction shapes. Saved judge
requests use their own reference version and request-bound descriptions; a
1.14 extraction cannot satisfy a 1.15 scoring contract.

The immutable catalog, source fixtures, arithmetic, evidence obligations,
thresholds, timeouts and production-budget classification are unchanged.
Existing reports and historical outcomes must remain intact. Any reassessment
of a saved answer is a separate artifact identifying the old report hash, new
reference, exact changed extraction fields, adjudicator and factual review.
An offline reviewed reassessment is not a new candidate run or a new live judge
measurement, and does not establish latency compliance.

The regression controls use independently annotated positive and negative
examples, including a counterfactual source with a different correct winner.
They verify extraction linkage, strict result/value checks and semantic failure
propagation. They do not measure whether a live judge reliably follows the
revised instructions.
