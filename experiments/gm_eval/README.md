# Private GeneralManager structural evaluation

This is the active experimental evaluation on `tk/local-siwc-eval`. It is outside
the distributable `src/` package. SIWC authentication,
refresh, HTTP transport, and prior result files remain unchanged.

The original reviewed catalog had 100 cases and 123 turns. The authorized simple
case extension (reference 1.2) produced 121 cases and 144 turns. Reference **1.3**
keeps **121 cases** and adds five K13 scope follow-ups for **149 turns**, with
DE/FR/EN **41/40/40**.
There are 15 core intents repeated at all five scales, ten introductory cases,
and 36 targeted cases. Counts by scale are 5:20, 10:23, 50:24, 100:26, 250:28.
`catalog_corrections.json` records the expansion and the E082 correction: both
P08 and P11 have zero planned revenue, while P02 has a missing plan. Original
catalog provenance remains recoverable and reference changes are versioned.

## Offline commands

Use this checkout's Python 3.12+ environment and force its sources on the path:

```sh
export PYTHONPATH="$PWD/src:$PWD"
.venv/bin/python -m experiments.gm_eval catalog --output /tmp/gm-eval-catalog
.venv/bin/python -m experiments.gm_eval fixtures --output /tmp/gm-eval-fixtures
.venv/bin/python -m experiments.gm_eval parity --output /tmp/gm-eval-parity
.venv/bin/python -m experiments.gm_eval harness --case E101 --case E105 --output /tmp/gm-eval-easy
.venv/bin/python -m experiments.gm_eval offline --output /tmp/gm-eval-offline
.venv/bin/python -m pytest tests/experiments/test_gm_eval_*.py -q
```

Choose a new output directory for each invocation; prior reports are never
replaced. `--case` may be repeated. `--scale` selects 5, 10, 50, 100, or 250.
`--profile` selects `weak-only`, `weak-fallback`, or `strong-only`. No CLI command
logs in or calls an external model. `offline` combines catalog/oracle validation,
all selected fixture groups, and the five default consumer parity conversations
E007/E008/E009/E010/E100. Scripted responses validate infrastructure, not model
performance, answer correctness, or production readiness.

The CLI saves `report.json` before deciding its exit status. Structured schema
metadata for a field named `status` is not an outcome label. Model task failures
and infrastructure failures retain their distinct report labels and return exit
code 1, as does failed parity; successful infrastructure checks return exit code 0.

Each fixture group starts a fresh Django subprocess and temporary SQLite DB,
uses real `DatabaseInterface`, `ReadOnlyInterface`, and `CalculationInterface`
classes and normal generated GraphQL registration, and checks the registry and
schema census for every selected case. Interface counts are respectively
3/1/1, 6/2/2, 30/10/10, 60/20/20, and 150/50/50. Auth and other helper models do
not count as exposed managers. Seed variants substitute distractor slots while
retaining the exact count. Read-only startup, relational integrity, populated
distractors and bounded calculation inputs are covered by regression tests.

## Reference conventions

The business clock is 2026-10-03 12:00 UTC. Last year means calendar 2025; last
month means September 2026; next year means 2027. RANK and TREND are separate
source snapshots, never inconsistent totals in one database. Monetary inputs
are exact decimal strings, EUR net unless explicitly specified, and final values
round half up to cents. Ties use ascending project code. Missing is not zero.

The added `project_costs` fixture uses actual net EUR costs grouped by PSP/WBS.
For calendar 2025, Engineering W01 totals EUR 3,500 and Project Management W02
EUR 1,200, overall EUR 4,700. Aurora's actual total is EUR 2,500. Separate plan
rows and out-of-period boundary rows must be excluded. These conventions are
model-visible fixture business context; no clarification is required for the
new cost questions. The `recent_shipments` fixture has 30 Aurora pieces in
September 2026 and excluded events in August and October.

K13 retains the exact original question at all five scales:
“Welche Stahlwerkstoffe haben wir aktuell in der Datenbank?” The first response
may ask an appropriate current/deleted-scope question or directly give the
complete grounded current inventory. Its actual second user turn is:
“Mit aktuell meine ich nicht gelöschte Datensätze; auch inaktive Werkstoffe gehören dazu.”
That follow-up requires M02, M04 and M05, preserving the steel family and the
non-deleted scope, without repeating the scope question.

For this Material manager, framework `is_active=False` means soft-deleted.
The fixture's separate business `active` field is not a deletion flag. M05 is
business-inactive but `is_active=True`, so it belongs in the current inventory.
All five seeded materials are non-deleted; no deleted row or deletion history
is invented. The structured chat query cannot request `includeInactive` merely
by adding an `include_inactive` filter; a requested deleted-inclusive query must
retain that capability limitation if the available tool cannot establish it.

Reference 1.2 incorrectly restricted this natural question to business-active
M02/M04. Version 1.3 records the original and revised dialogue/reference in
`catalog_corrections.json`, including the prior catalog hash. Prior live reports
are retained unchanged; this version change is not a retroactive pass claim.
Only independently validated external `response_mode` extraction can select
the optional first-turn clarification contract. The second turn always requires
the complete scoped result, and an absent follow-up is an incomplete case.

Active customers are C01/C02. Ongoing projects are P01/P03/P05. These explicit
questions still use direct scalar filters without unnecessary clarification.

## Integration and scoring

`harness.run_case` uses real production profile validation/construction,
`prepare_planned_turn`, `iter_planned_read_events`, default tool callbacks, and
durable `ChatConversation` records. Consumer parity uses the actual ASGI
`ChatConsumer`, the same provider scripts, and compares tool inputs/results and
conversation history. A matching failure can prove parity while the case still
has a capability gap. It is never a correct-answer result.

`profiles.siwc_factory(transport)` adapts an explicitly supplied existing SIWC
transport. The bridge never reads personal credentials or performs login.
Live evaluation requires explicit authorization and a completed offline gate.
The user authorized an initial bounded comparison of `gpt-6-astra` at medium,
`gpt-6.1-sol` at high, and `gpt-6-luna` at high reasoning, subject to exact
availability and settings verification. No answer-call service tier was requested.
Run artifacts, not offline checks, establish whether those calls occurred.
Live runs must record role profiles/reasoning and retain the complete schema. The existing
transport's pinned reasoning setting is checked instead of silently overridden.

`oracle.expected_turn` derives numerical references independently from source
seeds. `scoring.score_turn` keeps D discovery, I interface/source, R relations
and result completeness, C calculations, Q clarification/carryover, and A answer
and evidence separate. Alternate correct paths are accepted when their actual
returned fields support the source. Missing judgments stay unscored. The blind
adjudication API extracts facts from saved answer/evidence with strict schemas;
the deterministic oracle, not the judge, decides numerical equality. Future
turns, provider identities, and old scores are excluded from judge inputs.

Every semantic check must reference at least one eligible evidence ID or the
literal `answer`, which identifies the current answer. The `answer` reference
is allowed only for semantic checks; fact and citation references still require
eligible returned evidence. Invalid or stale judge output remains a judge
failure and is never automatically repaired into a pass.

For explicitly typed entity fields, identity comparison can recognize a database
ID through a code or uniquely matching exact name in eligible returned root-manager
rows. Nested row identities are not inferred from a root row.
Canonical identities come from complete fixture seed tables, never the expected
result subset or database row order. Raw extracted facts remain available and
normalization is audited. Conflicting, missing and ambiguous identities receive
no equivalence credit. Unresolved identities in supported typed fields remain
unscored; proven wrong entities still fail. Nested values inside constraints,
carryover context and planned-revenue objects retain the prior exact comparison.
Private identity metadata is excluded from judge inputs.
`reporting.case_result` aggregates scored turns without losing failed or missing
follow-ups; `reporting.summarize` accepts the scorer's dimension records directly.

Primary failures retain secondary flags: fixture invalid, interface capability
gap, harness failure, transport failure, judge failure, exhausted budget, model
task failure, passed. Coverage includes every attempted case. Unknown usage and
unavailable prices are null; tokens, tool calls, judge calls, latency and stronger
model routing are reported separately. No encrypted reasoning or credentials
belong in exported traces.

`efficiency.measure_run` records actual planning/provider/tool calls, reported
tokens, turn latency, and stronger-model escalation. Needless clarification
requires independent semantic adjudication. `compare_to_reference` compares
correctly answered, matched live runs by profile or schema scale, with explicit
reference repetition counts, medians, ranges and relative deltas. An observation
above that range is a review signal, not a universal budget or an exact required
tool sequence. Offline scripts cannot establish model efficiency. Plan at least
three repeats per live profile and report paired core effects separately.

## Production boundaries and historical baseline

The current executor preserves task-local tool feedback, including discovery,
schema reads and failed calls, independently of evidence eligibility. Requirement
descriptions are included in executor context. Earlier saved runs predate these
changes and retain their original diagnoses.

Read-tool contract 2 now uses the executable GraphQL schema. The historical
multiword-root, snake_case/camelCase, nested-filter registry and collection
selection gaps are covered by real generated-schema regressions. Current fixture
verification executes native names through normal chat tools; direct GraphQL
counterparts remain independent checks, never substitute model evidence.

Filter depth, manager exposure and resolver permissions remain actual schema
capabilities. Union/interface fragments and arbitrary non-manager roots remain
unsupported. Pagination metadata describes response coverage; semantic judgments
must still determine whether the gathered pages satisfy the user's request.

## Reference 1.4: internal grounding for ordinary answers

At the user's explicit request, the seven everyday contracts E101–E121 no
longer require a visible source line. This is a reference-policy change, not a
candidate improvement. Catalog questions, factual gold, filters, numeric and
identity checks are unchanged. The synthesis prompt and response are unchanged.

For these contracts, `citation_policy=internal_grounding` requires independently
validated per-data-fact answer quotes linked to actual eligible tool results,
source-role coverage, and an independent `answer_supported` check using those
sources. A tool call alone, model-authored references, missing support, invented
values, incomplete results or contradictory evidence do not establish success.
Visible citations remain separately reported. All other contracts retain the
legacy visible-citation obligation, including explicit source/provenance tasks.
Old expectations without the policy field retain the legacy behavior.

Keep old references and scores immutable. When re-scoring saved answers, retain
both reference versions and the original validated judge response, identify the
policy change and preserve hashes of the candidate and prior artifacts. Reuse
judgments only when their answer, evidence and requested semantic checks remain
identical; do not present a policy-only re-score as a new model run.

A review correction binds both parsed extraction and semantic judgment to the
SHA-256 of the exact normalized tool/history trace. Internal-grounding scoring
requires both bindings to match current evidence; changing row values, returned
rows, query arguments or history after adjudication requires revalidation and
cannot reuse the old verdict. Legacy saved judge responses can be re-parsed
against their unchanged original request offline; no new model call is needed.


Current read-tool inputs use GraphQL-native contract 2. Fixture verification and
scripted infrastructure checks were migrated to exact schema names; saved run
artifacts and business answer references are unchanged. Invalid Python names are
now request failures, rather than symptoms of the former translation mismatch.
Relation filter depth remains the depth actually generated by GraphQL. The
planner's compact catalog supports selective schema retrieval at larger manager
counts; the live provider still enforces its 200,000-character input guard.


### Pilot correction revision 1

This correction introduced grounding contract version 2 and the data fields
requiring eligible source IDs, without including expected values. A present
data fact must have answer quotes and nonempty source IDs. A claim genuinely
unsupported by eligible evidence uses `unsupported`, retains its quoted value,
and fails grounding. Missing links on a `present` data fact are an incomplete
judge response, not a demonstrated candidate failure. No IDs are filled in
automatically; numeric correctness and semantic checks remain independent.

### Prospective exact-ID linkage contract (v30)

Newly built judge packets now declare grounding contract version 3. Its
`evidence_binding` metadata states that grounding uses the exact-ID intersection
of the declared data fields' `fact_support` references and
`semantic_checks.answer_supported` references, restricted to existing eligible
evidence IDs. The blind instruction requires independent review of a witness
against both claims before choosing its identical, manager-qualified ID for
both references. Direct, nested and bridge source paths remain admissible when
their shared witnesses meet the unchanged source requirements. Different calls,
managers or rows never become interchangeable merely because an entity ID or
value matches. Additional witnesses for separate explanatory claims may remain
outside the common set; identical full reference lists are not required.

The parser never copies, adds, aliases or merges IDs. Unsupported facts and false
semantic judgments stay failures, and numeric correctness remains independent.
The scorer, source requirements, Gold and prior results are unchanged. Existing
version 2 requests retain their original contract and parsing behavior. Version 3
changes newly constructed request hashes; an old Judge response cannot be used
for a newly constructed request. This is a prospective instruction correction,
not proof that a future live Judge will select consistent witnesses. Synthetic
negative semantic reviews test that ID alignment cannot override an independent
false judgment; they do not establish automatic runtime detection of unsupported
answer claims. Any live evaluation needs independent review and a new bound
start release.

Discovery normalization includes paired successful search and schema results,
with their call, task and turn identities. Selected query managers do not create
candidates. Historical discovery must match a durable prior tool result that is
visible in the current production-prepared history.

`score_terminal_no_answer` handles verified terminal candidate failures without
an answer. The judge is intentionally not requested, answer dimensions remain
unscored, and the recorded failure category and actual available provider phase
are retained. Case aggregation includes `model_task_failure` in the complete
failure hierarchy and assessable denominator. These changes do not rewrite old
candidate runs, saved judgments or reports. New evaluation artifacts use a
separate revision; a changed request requires a fresh judge response after
review acceptance.


## Reference 1.5: consistent evaluation contracts

Ordinary answers now use internal grounding throughout the current catalog.
Actual eligible source references, answer quotes, required source coverage and
independent semantic review remain mandatory. Explicit source requests retain
visible citation requirements, including E098 and the supporting-evidence request
in E100. Policy checks use only the current and earlier questions. Saved legacy
references retain their recorded policy; missing policy still means visible
citations are required.

The unit-field policy `typography-v1` canonicalizes only these exact spellings:
`g/cm³` and `g/cm^3` to `g/cm3`, and `kg/m³` and `kg/m^3` to `kg/m3`. The score
records the raw and normalized unit. It does not rescale numbers, equate these
different unit scales, fold case, or normalize other answer text.

Entity-keyed measurement schemas describe what their keys mean without exposing
expected identities, measurements or cardinalities. A named entity in a scalar
answer can supply `result_ids` through unique visible evidence. Declared identity
paths such as `constraints.project` and inherited context paths allow the scorer
to canonicalize actual database IDs only through unique eligible returned rows.
Missing claims, missing evidence and ambiguous IDs are never filled from gold.
Every successful mapping records its original value, path and evidence proof.

For the initial natural steel-inventory question, the expected non-deleted scope
is unchanged. The scorer proves effective family and deletion scope from complete
linked query rows selected by the answer's extracted result IDs. Consistent native
pages also qualify when every page is independently bound: identical filters,
projection and ordering with an ID tie-breaker, contiguous pages from one, stable
page size and total, exact row counts, unique row IDs and consistent `has_more`
flags. Query fingerprints ignore recursive JSON object key order and normalize
omitted ordering directions to the actual GraphQL `ASC` default. Array order,
explicit directions and all other argument values remain significant; the answer
digest and original evidence remain unchanged.
Missing or duplicate pages and changed query arguments cannot establish
completeness. It separately
checks explicit scope claims against those rows. Answer facts remain unchanged:
an unspoken deletion flag is not inserted into `constraints`. Missing flags in
source rows, incomplete coverage, missing result rows, contradictory explicit
claims and failed semantic review still prevent a pass. The follow-up's incorrect
claim that business activity and soft deletion are contradictory remains a real
candidate failure.

Reference 1.5 preserves all 149 factual answer contracts and deterministic
expected values; catalog questions and bytes are unchanged. Old requests,
responses, references and scores remain immutable. Reuse an original judgment
only under its original request hash, never by relabeling it for a changed packet.
Changed E101-first-turn, E119 and E121 extraction packets require new independent
judgments of the saved answers after review. E119 also changes field descriptions,
so its old judge response cannot be reused despite the deterministic unit fix.
No answer regeneration is needed.

## Evidence-bound measurement corrections

Measurement revision `measurement-contracts-2` resolves nested identities only
through prior successful `get_manager_schema` results, selected typed relation
paths, and actual returned rows from the linked query. Single relations, page
items and connection edges retain row coordinates and schema/query call IDs in
the audit. Scalar foreign keys, unrelated nested objects, missing paths and
ambiguous or conflicting identities cannot establish a mapping. Root-query
completeness checks remain separate from nested identity evidence.

Only the `designation` fact accepts an exact returned name, an exact returned
alias, or two such labels in `name (alias)` form. The designation and result ID
must bind the same uniquely identified returned record; aliases must actually
have been selected and returned. Conflicting rows, foreign labels, unsupported
translations and partial/fuzzy matches receive no normalization. Raw facts,
matched labels, evidence IDs and row paths remain available in the audit.

These are deterministic measurement corrections: reference facts and blind
judge requests are unchanged. Re-evaluation of saved responses must use a new
output version and verify identical request/answer/evidence bindings; original
results remain immutable. No fresh model-quality claim follows from offline
re-scoring alone.


## Reference 1.6: offline remediation measurement contract

Catalog bytes remain unchanged (121 cases, 149 turns). Six `clarify_importance`
follow-ups explicitly request ranking, so E007, E018, E030, E042, E054 and E066
now expect all known ranked projects. Explicit top-one contracts retain their
original scope. Metadata records this reference correction separately from
product changes.

Adjudication schema 1.2 uses canonical clarification topics (`criterion`,
`metric`, `horizon`, `customer_identity`, `unit`). Required topics must be present;
extra topics must independently pass suitability review. `result_assertion`
distinguishes `none`, `some` and `unknown`: missing IDs never become a fabricated
empty result. Plan/year maps represent aggregate totals; optional component
breakdowns have a separate support check, including wrong extra claims.

`unit-labels-v2` retains typography aliases and adds the exact `Stück` → `pieces`
spelling only in `unit` and `context.unit`, with raw/normalized audit values.
It never rescales numbers, changes other words or accepts a different unit.
Contextual customer/scope references may use explicit visible prior user choices
only with current-answer quotes, actual eligible evidence and indexed verbatim
`history_quotes`. Independent review must verify unchanged, unambiguous scope.
No omitted numeric answer is filled from tools or previous answers.

Unknown query counts remain valid observed data but never prove completeness.
The consumer watchdog derives from the production evidence and synthesis budgets
plus cleanup allowance; infrastructure failures preserve received events and
record their origin. Innocent JSON text chunks retain their original spelling.

Historical files and validated requests are immutable. New packet shapes require
new independent adjudication; do not relabel an old response with a new request
hash. Offline comparisons and controlled extraction fixtures are not new model
performance. The genuine E008/E019 clarification failures and historical E101
second-turn semantic failure are not repaired by these measurement changes.
Historical reporting must distinguish 15 fully turn-assessed cases from 16 cases
with a known case outcome: E019 is known failing from turn 1 while turn 2 is unscored.

Structured clarification responses contain only `clarification.language` (`de`,
`en`, `fr`) and a nonempty unique `clarification.requirements` list drawn from
`criterion`, `metric`, `horizon`, `population`, `customer_identity`,
`record_selector`, and `unit`. The runtime renders fixed questions; this branch
accepts no free answer text, result claims, entity names, numbers, or evidence IDs.
The existing read plan must still resolve schema/identity evidence. Data answers
continue to require nonempty unique eligible `evidence_ids`; mixed answers and
questions use that grounded branch. The normal fallback, delivery, and persistence
paths apply to both response shapes.

Reference 1.7 / adjudication schema 1.3 replaces the absent-answer fact
`clarification_repeated` with the required semantic check
`no_repeated_clarification`. Its Boolean and reason are bound to the current
answer and visible conversation. A failed check requires verbatim repeated-question
and resolving user-choice witnesses; a pass requires an empty witness list.
Missing, contradictory, or stale judgments remain unscored. A new unresolved
question is not automatically a repetition. Numeric gold and historical results
are unchanged; older saved requests retain their own versioned response schemas.

Reference 1.8 / adjudication schema 1.4 / measurement-contracts-4 separates
requested result membership from explanatory claims. An excluded tie example is
not an extra ranked result; its correctness still belongs to `answer_supported`.
Annual maps contain annual values rather than a separately labelled multi-year
total, whose correctness is also independently accountable. Missing/extra years,
wrong totals and extra actual result members remain failures.

Nested identity-list descriptors apply per element to `constraints` and `context`
paths. Normalization uses uniquely selected, manager-scoped evidence rows and
retains per-element provenance; it never removes duplicates or extra identities.
Effective scope may use an unchanged explicit user choice with current evidence
and indexed history witnesses. Assistant history is not authority. Currency,
tax basis, metric identity and quantity basis remain distinct concepts; no generic
net/gross or qualifier alias is introduced. Required explicit exclusions of
incomplete periods remain required even when all reported totals are correct.

New extraction metadata carries `adjudication_schema_version`; reference 1.8
requires 1.4. Legacy saved requests/responses remain parseable under their original
schema, but cannot be relabelled or scored as new judgments. Gold facts and catalog
bytes remain unchanged. Offline controlled judgments test validation and scoring;
they do not establish improved live model extraction or answer quality.

The v28 evidence adapter assembles compatible contract-2 Overview/Detail/Full
fragments for each exact manager and snapshot. Overlapping definitions must
agree, including enums and defaults. Typed relation paths may use declared
manager references from an Overview; intermediate wrapper definitions must be
loaded. Every used manager must be in the query's manager binding. Proofs retain
actual row coordinates, schema call IDs and used manager snapshots. Identical
legacy full captures remain supported; conflicting snapshots or definitions do
not produce identity evidence.

The Judge echoes the context hash only at
`semantic_checks.no_repeated_clarification.context_sha256`, and only when that
check is requested. Extra top-level hashes remain invalid. A stated absence of
a filter omits that constraint key; a stated unrestricted scope is `{}`.
Actual narrowing, contradictions and unknown extra claimed filters remain in the
facts and still fail exact comparison when incorrect. Identity extraction uses
existing manager/path descriptors to distinguish requested selection targets
from result members and explanatory entities. Unresolved target IDs stay
unresolved; explanatory claims remain subject to `answer_supported`.

`build_adjudication_request` retains optional `schema_transport_sources` as
hash-bound audit proofs for uniquely matched prior schema calls and separate
verified durable tool metadata. The pure builder and saved-response validation
need no provider construction or Django setup. Old requests without the optional
proofs retain their original digest contract. `judge_messages` validates sources
and uses the existing request-local lossless schema codec at explicit current
schema outputs and verified history text positions. The codec reconstructs all
logical packet data, text formatting, roles, indices, hashes and source bindings
before sending. Unbound or ambiguous history stays literal; a malformed bound
projection fails before provider entry. The original request and projection
receipt remain available for audit. Schema transport is data, not new evidence
or application instructions. There is no truncation, full-schema fallback, cache
bypass, retry or increase of the 200000-character provider guard.

New prompts, packet hashes and transport projections require fresh independent
adjudication after review and bound preflight. Structural probes of saved sources
or body sizes do not rejudge historical answers, change scores or demonstrate
live quality. The fixed reference denominator remains 46, including unscored
cases, with 42 passes required for the greater-than-90-percent gate; the separate
catalog contains 121 cases. Candidate/Judge Astra Medium, the 180-second diagnostic
limit and separate 90-second production marking remain unchanged.
## Prospective quantity-basis extraction

The blind judge names explicitly gross actual shipment results or aggregate
totals `gross_shipped_quantity`, including when no gross/net comparison is
requested. Historical year-by-year series of unadjusted shipment quantities
remain `shipped_quantity`; a supported gross qualifier alone does not convert
that series into a gross/net comparison. Existing unadjusted shipment-plan
pieces also retain `shipped_quantity` with `existing_plan`. Net-after-returns,
weight, revenue, provenance and unsupported qualifiers keep their distinct
checks. Literal metric and clarification-topic scoring remains unchanged.

This is a prospective request-contract clarification. Historical results,
reference expectations and saved judge responses are retained without
reclassification. Offline wire replays verify contract delivery and do not
measure model accuracy.


### Open outlook clarification correction (reference 1.9)

Open outlook requests in the generic `clarify_actuals` contract require
`criterion` and `horizon`: the standard defining the judgment is unresolved.
A defined development analysis (`clarify_development` and `final`) continues
to require `metric` and `horizon`, because the analytical objective is already
defined and its measurement is missing. The scorer keeps these topics distinct.
Missing/wrong questions, unsupported results and irrelevant clarifications still
fail their existing checks. No case-ID exception or topic alias is used.

The frozen 121-case/149-turn catalog bytes and prompts remain unchanged.
`catalog_corrections.json` explicitly supersedes only the legacy first-turn
metric/horizon prose for E019, E031, E043, E055 and E067; their authoritative
version 1.9 oracle topics are criterion/horizon. All numeric, unit, source and
follow-up expectations are unchanged. Historical scores and Judge responses
remain immutable. Any reevaluation using stored complete evidence is published
as a separate offline report, with its old/new oracle and input hashes. It is
not a new candidate response or a new Judge result.


### Context-specific outlook clarification, reference 1.10 (v38)

The exclusive criterion requirement in reference 1.9 was rejected during
independent review. First-turn `clarify_actuals` now asks for the unresolved
evaluative basis and horizon: its explicit `required_topic_groups` rule is
`[["criterion", "metric"], ["horizon"]]`. At least one independently extracted
topic from each nonempty group is required. This is a context-specific
alternative, without renaming observations or equating criterion and metric
in other contracts. Defined `clarify_development`/`final` analyses still use
the unchanged strict metric/horizon rule.

`facts.clarification_topics` remains a valid representative criterion/horizon
extraction shape; the Q rule separately records all acceptable decisions.
Blind Judge shapes and instructions remain unchanged. Topic presence cannot
bypass independently validated `clarification_suitable`, unresolved/relevant
questions, or the prohibition on premature result assertions.

The bounded scorer change adds only the explicitly named group comparison.
The existing `required_topics` implementation is unchanged. Empty/invalid
groups, unknown labels, duplicate or malformed observations fail. The generic
contract covers all five scales; correction entries list their instances for
provenance, without dispatching by case ID. Reference 1.9 entries remain
historical; 1.10 supersedes their exclusive criterion requirement. All catalog
bytes, questions, numeric facts, source and follow-up obligations are preserved.
Stored-case rescoring is a separate offline artifact and measures no new model
performance. Independent v38 review is required before any inference release.
# Offline identity normalization

Numeric/string IDs are normalized only from eligible selected typed query rows
and explicit contract identity paths. A row selecting neither code nor name
contributes no identity and cannot invalidate an independent unique mapping.
Explicit unknown or contradictory identities remain blocked. Reachability source
projects and the project inside planned revenue have scalar identity semantics.

Native schema overviews now carry only exact native type signatures along exposed
relation paths, allowing collection `items` selections to be bound without whole
wrapper or full schema loading. Historical captures missing those declarations remain unresolved; the
evaluator must not synthesize them from JSON shape or a later schema. Offline
normalization audits are separately versioned and are not fresh model results.
Root identity fields must actually be selected. Incomplete queries cannot prove
an identity until exact, contiguous query windows cover the root count with
unique IDs and complete nested collections. Identical repeated windows are
deduplicated; conflicting metadata, changed filters and failed pages are rejected.

Offline completeness revision r1 binds nested metadata to actual unique selected
fields in both short and structured selections. Pages require selected integer
totals matching returned items. Legacy Connections require selected matching
totals or both selected directional pagination flags false; unknown, partial or
contradictory selected metadata cannot certify identities. Extra unselected
output counts/flags supply no proof. Consistent selected camel/snake aliases are
checked together. Product overview projection and offline identity policy are
separately source-hash-bound; this revision does not change historical scores.

Offline completeness revision r2 distinguishes native GraphQL leaf selections
from object sub-selections. Scalar/enum fields named `items` or `edges`, including
scalar lists and custom JSON values, do not represent pagination wrappers.
Selected compound collections retain all r1 metadata requirements, including
rejection of mixed or duplicate compound selections. The distinction follows
the selection contract rather than guessing from returned scalar JSON values;
historical schema evidence and scores remain unchanged.

The r2 manager-role correction reuses the exact query ID and row coordinates
from successful typed/snapshot-bound identity-path proofs. Manager objects can
have ordinary relations named `items` or `edges`; their compound children are
still traversed and genuine Page/Connection wrappers still require all r1
metadata. Mixed or duplicate compound `items`/`edges` selections remain rejected
at manager objects too, so ambiguity cannot suppress child-wrapper recursion. Roles are obtained through the existing typed query reader, including
queried managers without their own evidence record; this creates neither a new
evidence record nor identity credit for those managers. No role is guessed from
raw IDs, output names, arbitrary manager membership or later schema fragments.

Offline identity correction r3 rejects leaf-only (or empty) selectors when
earlier bound schema definitions prove a composite object/reference field.
Only available matching definitions and modern overview manifests are used;
unknown/unbound schemas, scalar/enum/JSON leaves and native manager relation
selections retain their prior behavior. No schema loading or history changes.


### Isolated r4 Judge and presentation evaluation review

The r4 revision is a separate offline evaluation policy based on the accepted
r3 identity/completeness implementation. Existing v51 run sources and historical
reports are immutable. A saved-answer counterevaluation reuses the exact answer,
trace, extraction and semantic judgment; its results measure policy changes and
must not be reported as new model performance or a current Live acceptance rate.

An invalid or out-of-scope adjudication envelope is a measurement failure. Its
retained extraction and semantic judgment cannot authorize answer scores. The
trace remains available for diagnostics, and a correctly bound completed
adjudication can still grade a candidate answer with recorded candidate failures.
E018's originally invalid saved Judge response remains unscored; this review does
not obtain a replacement judgment or retry it.

Presentation compatibility is intentionally narrow: equivalent actual net
project cost metric labels, equivalent available OLS forecast presentation, and
additional exclusion claims backed by a complete selected query. Compatible
representations retain numeric, year, unit, manager, identity, provenance,
completeness and semantic checks. Unknown/contradictory labels or additional
claims are rejected. Missing facts, history, schema definitions, coefficients,
assumptions or evidence are never supplied by the normalizer.

Unclear domain requirements are documented separately from representation bugs:
manager meaning, implicit approval filters, mandatory forecast explanation,
plan-versus-actual source requirements and optional-versus-required cost
breakdowns need explicit contract decisions. Their Oracle contracts are retained.

Numerical cost support must come from the selected ledger rows cited for each
amount and total. Complete query pages certify the requested population only
after effective root/relation arguments and loaded input defaults are checked;
ID/search/exclude restrictions cannot certify a whole-period cost or shipment
history. Captured JSON and GraphQL defaults must agree with exact scalar types.
This validation uses already bound schema evidence and makes no schema calls.

The additional saved E097 attempt10 remains failed: its generic first-turn
clarification is inadequate, and correct second-turn values/bands/total do not
supply absent regression details or mandatory assumption disclosures. No saved
semantic result or business Oracle requirement is changed by representation
normalization. Explicit malformed failure flags or non-object traces in a saved
Judge envelope remain measurement failures; the original saved input is kept.
