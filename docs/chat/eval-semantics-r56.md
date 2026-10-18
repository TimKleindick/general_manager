# Authorized exclusion and cost-summary semantics

Tim clarified the existing evaluation on 2026-10-16: an excluded characteristic
also excludes an intersection containing it. Project costs are primarily a sum;
additional breakdowns can help for few results. Correctness must not depend on
omitting that optional presentation. No fixed definition of "few" was supplied.

Reference 1.12 implements those two decisions at the existing source-row oracle
and blind-extraction owners. It adds no product-side case recognizer, scorer
fallback, threshold or special route. Existing product definitions already say
to group project costs by WBS when requested. Source fixtures and catalog
questions remain unchanged.

| Case | Required meaning in 1.12 | Preserved strict obligations |
| --- | --- | --- |
| E072 | Brass present; Copper absent from the whole project's material set, including mixed projects | Exact project membership, Brass identity and an explicit excluded-Copper constraint; unrelated materials remain allowed |
| E116 | Actual net project-cost sum, EUR 4700 for 2025 | Exact arithmetic, metric, EUR, year and `[2025-01-01,2026-01-01)`, actual kind and ProjectCost grounding |
| E117/E118 | Explicitly requested WBS breakdown and total | WBS membership, names, group values, WbsElement evidence and all ordinary cost obligations |
| E086 | Existing supplier-approval interpretation | Unchanged; this decision does not authorize a new approval policy |

For E116 the sum comes directly from eligible dated actual ledger entries,
independently of whether WBS assignments or labels are available. WBS/project
details are optional. The existing required `additional_breakdowns_supported`
semantic judgment checks every offered assignment, label, amount, unit, period,
scope and completeness claim against eligible evidence. A correct total cannot
override a failed detail judgment. A missing/invalid judgment is a Judge failure,
not a pass. A breakdown claimed complete must reconcile with the total;
explicitly partial examples need not exhaust it. Blind extraction keeps those
claims separate and cannot compute an omitted total from rows or tools.

Version 1.12 is the new default for catalog/report/oracle/harness Python entry
points and ordinary CLI execution. Explicit selectors 1.10 and 1.11 remain
supported. Their 149 contract and blind-shape hashes are tested separately;
1.11 hashes were captured at a1e5ae0b before this change. The 1.12 request marker
is bound through the existing digest, validated extraction and scorer check.
The adjudication envelope stays 1.5; the evaluation revision is
`measurement-contracts-6`. Component and forecast meanings introduced by 1.11
remain inherited. No saved request, response, score or historical report is
rewritten or rescored.

Positive and negative offline controls cover pure Brass, Brass/Copper,
Copper-only, neither, an additional permitted material; total-only cost answers;
wrong/missing totals, metric, units, source kinds, dates and constraints; failed
or missing optional-detail judgment; explicitly requested WBS omissions; absent
WBS metadata; version tampering and both historical reference snapshots.
Controlled Judge envelopes test protocol/scorer behavior and make no claim about
future live Judge reliability or model quality.

Publication remains confined to PR #517. Normal regression/static checks and
independent review are required. No merge or new live Eval is part of this
change. The Cloud-Linux A/B route remains technically blocked before execution;
do not use a local or unbound replacement run as evidence for that diagnostic.
# Bound open clarification choices

New Judge packets for an explicitly required clarification carry
`clarification_contract.version=bound-open-choices/1`. Its topic groups express
the already bound unresolved decision, not extracted candidate claims or numeric
Gold. A group permits one of its topics. A manager describing one available
forecast does not by itself select that analysis for an open user request.
Additional questions must still be relevant and unresolved; user choices and
applicable definitions remain binding. Missing topics stay missing, and false
semantic judgments remain false. No check is automatically passed or rescored.

The policy and groups are bound by the new request digest. Archived requests
without this field retain their original Judge instruction branch. Catalog,
reference expectations and historical raw requests, answers and scores remain
unchanged. This is a prospective Judge policy change, not evidence that a new
live answer has passed.
