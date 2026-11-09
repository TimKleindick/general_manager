# Native list structure preparation

This follow-up to #583 starts from `d05ce4e8`, the previous verified native-list
optimization. It applies the user's fixed-definition assumption to repeated
structure work in ordinary database lists. The earlier
[list optimization](list-optimization.md) remains a historical checkpoint.

## Implementation and contract

The operand authorizer prepares standard ORM field names, aliases, scalar
lookup structure and additional-rule budget classification lazily once per
authorization call. It reuses the existing alias, relation-type and permission
analysis owners. It stores no actor/object permission result and reuses no
Permission instance. Every original runtime field decision, callback, candidate
occurrence, off-page/nonmatching check and related-row gate remains.

The native database list resolver explicitly enables preparation. Search,
request and calculation sources, and direct helper calls without the opt-in,
retain their existing path. General traversal still handles properties,
ambiguous aliases and multi-step relationships. Eligibility checks the actual
interface/read/support implementations and their backing metadata stores;
custom methods, descriptors, mappings and executable/delegated Permission
classes retain dynamic behavior. The Permission owner only accepts exact
builtin read-rule containers; executable container subclasses and direct
settings-backed policies retain their existing evaluation sequence. The
eligibility probe never invokes a dynamic Permission descriptor. Concrete
manager, interface and Permission identities are checked when using a plan. Identification validation remains
per operand and per object.

Standard schema and declarative permission definitions, including nested rule
dictionaries/lists, are assumed unchanged during one planned authorization
call. Mutating these definitions from a callback is outside this static
contract. The next call reads them again. No global plan or decision cache is
introduced. A permission callback may still deny later or raise: those calls
are not skipped or memoized. Examples in KH are registered `isOwnDataset` and
`isMyEmployee` rules, whose actor/object decisions remain runtime work.

## Regression evidence

The new real-resolver test initially failed on the prior source: increasing
the candidate set from one to twenty grew metadata calls from 7 to 121,
accessor-map reads from 5 to 100 and additional-rule analyses from 6 to 120.
The prepared implementation passes the same constant-work guard, with the
same count, page and order assertions. Instrumentation observes real function
entries without replacing canonical methods.

Prepared/unprepared regression controls cover relation annotations with
`_general_manager_class`, dynamic descriptor-backed metadata, an identification
property that raises on its second read, alternating Permission descriptors,
and executable rule dictionaries/lists. Each defect was reproduced before
repair. Tests preserve the relation gate, access sequence, callback decisions
and errors. A further probe checks that an unknown container's metaclass is
never evaluated. Scoped re-review found no remaining blockers in these fixes.

## Repeated KH comparison

The unchanged KH harness ran in A–B–C–C–B–A order: GM 0.86.4, frozen
`d05ce4e8620ed7058f7c6fab80be795e705cc81a`, and the prepared implementation.
Two fresh processes per variant provide ten cold and ten warm samples for
each case. No other test suites ran during timing. All 13 cases have equal
fixture, request, response and result hashes across every sample and variant.

Warm median [inclusive IQR], milliseconds:

| Complete flow | GM 0.86.4 | Previous PR | Prepared | SQL: baseline / previous / prepared |
| --- | ---: | ---: | ---: | --- |
| Simple search | 1.383 [0.165] | 3.167 [0.204] | 3.082 [0.242] | 2 / 2 / 2 |
| Rich search | 5.321 [0.303] | 7.636 [0.133] | 7.706 [0.056] | 13 / 12 / 12 |
| Anonymous search | 1.815 [0.124] | 1.853 [0.054] | 1.867 [0.201] | 0 / 0 / 0 |
| Bounded month | 14.733 [0.843] | 18.566 [0.611] | 18.700 [0.268] | 7 / 7 / 7 |
| Complete year, events | 57.166 [3.436] | 97.751 [3.534] | 99.602 [1.639] | 29 / 29 / 29 |
| Complete year, all 365 days | 103.117 [5.884] | 150.771 [5.248] | 152.290 [3.675] | 29 / 29 / 29 |
| Year via monthly windows, events | 149.144 [8.048] | 195.808 [4.588] | 197.432 [1.808] | 73 / 73 / 73 |
| Year via monthly windows, all days | 196.275 [10.180] | 249.218 [5.897] | 251.565 [3.554] | 73 / 73 / 73 |
| Private outsider month | 18.532 [1.019] | 20.276 [0.493] | 20.629 [0.223] | 43 / 48 / 48 |
| Raw 1,000 bookings, ten pages | 275.524 [13.075] | 1067.719 [27.312] | 519.385 [1.557] | 160 / 170 / 170 |
| Summary over 1,000 bookings | 26.788 [1.068] | 27.743 [0.599] | 28.270 [0.454] | 4 / 4 / 4 |
| Summary list over 1,000 bookings | 29.015 [0.969] | 30.727 [0.649] | 30.766 [0.504] | 6 / 6 / 6 |
| Anonymous summary list | 3.207 [0.191] | 1.383 [0.263] | 1.397 [0.193] | 2 / 0 / 0 |

**Raw bookings take 51.4% less time: 1,067.719 → 519.385 ms.** The remaining
ratio to GM 0.86.4 is 1.89×, down from 3.88× in this comparison. The prepared
warm range is 516.973–520.380 ms. Cold medians are 281.906 / 1078.718 /
516.923 ms; cold ranges are wider and include initialization/cache effects.
This is not performance parity or a claim that every other case got faster.

The final diagnostic records field-type reads falling from 60,030 to 50,
GraphQL-property metadata reads from 50,040 to 50, accessor-map reads from
50,000 to zero, and additional-rule analyses from 60,000 to 50. All 50,000
operand visits and 68,000 runtime field decisions remain. ORM hydrations stay
at 11,140, and SQL stays at 170 versus the baseline's 160. The ten native count
statements and the extra runtime permission work remain separate costs.

IQR/range describe observed dispersion, not confidence intervals. Timed
environments use Python 3.14.10, Django 6.1.3, SQLite 3.53.1, UTC and
`PYTHONHASHSEED=0`. Supported filelock versions differ: 3.24.3 for the baseline,
4.2.9 for both newer variants. Timing covers backend execution and JSON
encode/decode, excluding HTTP/session middleware, browser, network and
production services. Cold clears Django caches in an initialized process;
warm follows an untimed warmup. Every request has a fresh calculation context.

[Per-sample comparison evidence](query-structure-comparison.json) retains
commands, source bindings, equality hashes and every sample. Full raw traces
and receipts are at `/tmp/kh-gm583-structure-final/final-performance`.

## Validation

Focused verification: 76 tests plus 25 subtests on the local Django 5.2
environment; 134 tests plus 53 subtests on Django 6.1. Ruff, formatting
(958 files) and strict mypy (355 source files) pass.

| Gate | Final result |
| --- | --- |
| Full GM suite, Python 3.12 / Django 5.2 | 16,587 passed, 130 skipped, 2 warnings, 2,601 subtests; exit 0 in 757.28 s |
| Full KH backend, Linux/SQLite | 7,219 passed, 8 skipped, 1,279 subtests; exit 0 |
| KH PostgreSQL contracts | 20 passed, 7,227 deselected; exit 0 |
| Original booking and complete-year calendar gates | 3 passed, 4 subtests; unchanged assertions |
| Ruff, formatting, strict mypy, all pre-commit hooks, strict docs build | Passed |
| Independent scoped review and re-review | No remaining findings in the changed preparation/fallback surface |

The full GM run had the required local loopback and Docker access. The
[source-bound verification receipt](query-structure-verification.json) retains
commands, log/JUnit hashes and manifests: all 901 frozen Python inputs remained
unchanged. The candidate package manifest also matches both final comparison
processes and every KH backend gate. Temporary raw verification logs remain at
`/tmp/kh-gm583-structure-final/`; the diagnostic profile is at
`/tmp/kh-gm583-raw-profile/structure-plan-verified/profile.json`.

KH uses the same isolated consumer adaptations documented in the
[compatibility report](kh-compatibility-validation.md), with the original dirty
checkout preserved. These results do not claim hosted KH green for a newly
published GM release. Frontend source/API shapes are unchanged; previous
frontend evidence remains separate, including the known SSO retry flake.
No merge, release or KH dependency publication is included.
