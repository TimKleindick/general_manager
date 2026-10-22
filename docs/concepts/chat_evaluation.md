# Chat evaluation and reproducible comparisons

Framework correctness and model answer quality need different evidence.
Deterministic tests can prove that a validator rejects stale schemas or that a
scripted conversation follows the production scheduler. They cannot establish
that a live model gathers the right population or answers correctly. A live run
needs a known source, case selection, model configuration and complete outcomes.

## Choose the appropriate entry point

| Entry point | What it measures | Where it runs |
| --- | --- | --- |
| Installed `python -m general_manager.chat.evals` | Configured provider against packaged datasets, through the production scheduler | A configured Django project |
| Deterministic scheduler tests | Contract, state, evidence and failure behavior with explicit role pins | Source checkout, no live model required |
| Source `experiments.gm_eval` harness | Structural fixtures, oracle, native conversation parity and scripted infrastructure | Source checkout; not distributed product API |
| Explicit GM/SIWC live driver | A separately authorized, source-bound provider/judge experiment | Review-controlled experiment environment |

Follow [the installed evaluation how-to](../howto/run_chat_evals.md) for normal
provider comparisons. Repository experiments have a separate
[development guide](https://github.com/TimKleindick/general_manager/tree/feature/chat-schema-executor-v53/experiments/gm_eval) and do not form a hidden
production or installed CLI fallback.

## Execution and provider completion

New installed/source evaluation runs identify `chat-evaluation/5`. They use one
thin read-only adapter over the production planner, executor, synthesizer and
native tools. The chosen provider is pinned across the roles; deterministic
provider/tool replacements require explicit fixture configuration. Mutation plans
stop at `read_only_eval_mutation_plan` and execute no mutation fallback.

The packaged adapter's default dispatch cap is **10 requests per turn** across
all roles, including fallback and failed attempts. `max_provider_requests` has a
supported range of 1–64. The compatibility alias `max_tool_iterations` selects
the same cap; passing both is invalid. This evaluator cap is distinct from the
normal planned scheduler's round accounting and deadlines. Separate structural
profiles can declare different ceilings and must report them honestly.

A completed provider round requires exactly one terminal `DoneEvent`, followed
by iterator completion and successful cleanup. EOF alone, a late event or a
cleanup exception is not a successful round. Provider-specific adapters validate
their own terminal reasons; the Responses adapter requires its stricter
`response.completed` boundary. Custom providers must emit the neutral terminal
and close their streams correctly.

Observed token usage is retained for quota/accounting even if cleanup later
fails; that observation grants no continuation or success. Unknown usage is not
an observed zero. A transport-raised timeout remains a provider failure; only the
round's own expired timeout establishes that round's deadline. Cancellation is
preserved rather than converted into a quality verdict.

## PASS, FAIL and UNSCORED

- **PASS:** the completed result satisfies its selected case contract.
- **FAIL:** a completed wrong/empty answer or verified model-task failure fails
  the quality contract. A diagnosed rejected-action cycle is a model failure.
- **UNSCORED:** a technical provider, transport, deadline or budget failure
  prevents a valid quality assessment. A partial answer does not erase a proven
  technical failure in another task. An otherwise passing answer also remains
  UNSCORED when the quantity-coverage check cannot verify an additional claim;
  this new abstention never replaces an existing FAIL, including across turns.

Both FAIL and UNSCORED prevent readiness. Keep the entire selected denominator;
never drop unscored cases to improve the pass rate. An empty selection is not a
100% pass: direct suites reject it, CLIs fail before provider construction and
readiness records `run_status=empty`. Report a stopped run's not-executed cases
separately rather than crediting historical results.

Stable public error wording alone cannot establish the technical origin of a
failure. Current runtime audit origins and matching task/turn evidence distinguish
scheduler/provider failures from a model declaring a block. A model cannot obtain
an unscored exemption by using a technical-looking public code.

### Packaged answer checks have limited coverage

The installed report's **Answer quality** score measures expected keyword
coverage and explicit exclusions. A score of 1.0 does not establish that every
answer claim is correct. The product contract separately checks required values,
selected contradictions, and a bounded list-membership rule.

Contract `/4` adds the bounded list rule. It applies where the hard contract
requests result values without additional answer keywords outside those values;
mixed schema/data answer contracts are left to their other checks. It checks an
initial bare comma/conjunction list or an initial bare
bullet/numbered block containing at least one observed result value. Each inline
item must be a single label token or an exact observed scalar; initial bullet
blocks also accept unpunctuated multiword labels. Two comma-separated
fragments without a conjunction are left to prose review. Matching ignores case
and surrounding Markdown emphasis. Only aligned successful `query`/`query_*`
results' `data` values support membership; schema/discovery text and failed
results do not. Unrecognized list syntax is not recorded as a passing list check.

This rule does not extract arbitrary prose, unknown inline multiword labels,
punctuated multiword bullet sentences, prefaced or later lists,
negated/excluded membership, entity-to-field associations,
quantities, units, or derived calculations. Contract `/5` additionally abstains
on digit-bearing prose after a recognized list, excluding supported bare numeric
labels and list numbering. This is conservative: a matching scalar in tool data
does not prove the prose claim's entity, metric, unit or scope. Abstention reasons
are separate from contract violations and execution errors and appear in traces
and verbose reports. Main numeric answers outside the recognized-list grammar
are not treated as additional claims. Written-out quantities and arbitrary prose
remain outside this bounded detector. Independent semantic claim adjudication
remains necessary; the source experiment's separate judge is described below. New checks do not reinterpret historical
reports or demonstrate new live-model quality.

## Freeze the actual comparison cohort

`select_eval_cases()` freezes ordered complete case definitions once.
`EvalSelection.iter_cases()` returns fresh cases for each candidate; its detached
manifest includes dataset/case identities and hashes of the conversations and
expectations. Supply either `selection=...` or dataset/tier/tag arguments to the
suite, not both. CLI comparisons reuse that same selected cohort.

Readiness comparison requires the same ordered case definitions, fixture
identifier, gate and evaluation/scoring contract. Provider/model/prompt/tool
fingerprints can differ and must be retained. Selection filter expressions
explain provenance; the actual cohort determines comparison identity. A fixture
identifier is not a snapshot of an arbitrary project's changing database.

Historical contract `/1`, `/2` and `/3` reports remain readable. Different contracts
or missing cohort proof cannot establish a comparable new regression baseline.
Create a new baseline explicitly and preserve old files. Do not reinterpret a
historical score using a new source, extraction schema or reference default.

## Structural reference and semantic adjudication

The current structural experiment default is reference **1.17**. Public catalog,
oracle, report and harness defaults select it. Reproduction of an archived run
must pass its original reference explicitly. References 1.10–1.16 retain their
original expectations and blind extraction shapes; prospective reassessment is
a separate artifact with its own source/reference/request/answer hashes.

The blind judge extracts only claims actually asserted in the answer, with exact
support quotes and eligible independent evidence. It must retain wrong, missing,
extra or contradictory claims instead of filling them from the oracle. Reference
version, extraction policy and response-envelope version are distinct selectors.

Useful current boundaries include:

- Result membership and asserted ranking are separate. An ordered list/table or
  an explicit single winner can assert order; unordered membership does not.
- A selected single winner can be explained by comparison rows without declaring
  every comparator a winner. Every volunteered comparison, value, unit, population
  restriction and completeness claim must still pass `answer_supported`.
- Positive selection targets and excluded identities retain distinct roles.
  Logical selection sets must include all required members and no extras.
- Missing values remain unknown rather than zero. Known-values-only rankings,
  unconditional all-candidate rankings and explicitly unknown coverage are
  different claims.
- Only forecast scenario year entries accept either ordered `[low, high]` or
  the closed `{"low": low, "high": high}` representation under reference 1.17.
  Missing/extra years or keys, reversed bounds and contradictory claims fail;
  other dictionaries and lists retain their normal comparison rules.

## Equivalent metric source routes

An evaluator's trusted oracle may declare `equivalent_routes` for complete
same-metric inputs. This is not acceptance based on matching final numbers.
Actual selected successful queries must prove the exact eligible population,
fields, values, period, currency, pagination completeness and answer usage.
Unknown quantities/prices remain unknown. Foreign-currency inputs need selected
rate/date/source matching the unique independent conversion reference; a claimed
rate alone is insufficient.

A schema or identity query whose projection omits the metric inputs is not a
metric route. A qualifying projection with wrong returned inputs fails. Unused
correct evidence cannot replace wrong evidence actually used by the answer.
Expected input rows remain evaluator-only and do not enter the blind packet.

Trusted `source_restrictions` apply to canonical and equivalent routes. Explicit
physical provenance obligations remain mandatory. The scorer does not infer an
exemption from an answer's prose, a case ID or a manager name. All other result,
unit, arithmetic, unknown-value and semantic checks remain in force.

## Production and diagnostic limits

Production defaults are 90 seconds for planning/evidence and 30 for synthesis.
A diagnostic run may explicitly use 180/90 or a 300,000-character final JSON body
guard. Those settings describe that experiment only. Historical 200,000-character
guards and request ceilings stay bound to their original artifacts. Report the
actual settings and full serialized-body sizes; a timeout under diagnostic limits
is not measured production compliance.

Lossless transport replay, offline fixture parity, code review and a green test
suite each prove their own boundary. They do not create a new live pass, latency
measurement or scale result. Retain full request/response/tool evidence and source
hashes for each new run. Treat auth, quota, transport and integrity stops as stops;
do not automatically retry an uncertain inference POST or resume an old request.
