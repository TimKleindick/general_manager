# Blind ranking extraction

Adjudication schema 1.5 specifies ordered result extraction and a closed shared
ranking coverage vocabulary. This changes the measurement contract for new
requests; existing frozen schema-1.3/1.4 requests and responses remain bound to
their original schema, request hash and answer hash.

When `ranked_ids` is requested, the answer's visible presentation order in a
list or table is an asserted ordered fact. A ranking word or metric label is
not required. The Judge must preserve that order, resolve names only from
unique visible evidence, and retain wrong, missing or extra result entities.
An absent list stays absent. Neither the builder nor scorer supplements IDs
or substitutes the order of tool rows or reference results.

`ranking_coverage` uses the same closed vocabulary in the Judge instruction,
fact schema and reference metadata:

| Value | Meaning |
|---|---|
| `known_values_only` | The answer limits its ranking/winner to known values and qualifies missing competitors separately. |
| `all_candidates` | The answer asserts an unconditional ranking/winner for the requested candidate population. |
| `unknown` | The answer explicitly leaves coverage indeterminate. |

No asserted coverage uses `null` with absent support. These labels describe
answer claims; the schema lists every alternative and does not disclose the
expected label. Noncanonical strings such as `highest_known` are rejected for
new schema-1.5 responses rather than repaired silently. Actual quotations and
independent eligible evidence remain mandatory. A false unconditional winner
or unsupported qualification still fails.

The logical task reference remains version 1.10: IDs, numeric values, selected
populations and source requirements are unchanged. Only its measurement-schema
metadata advances to 1.5. Previously frozen results and Judge outputs are never
rewritten, relabelled or credited to a new candidate run. Offline request rebuilds
prove contract and transport behavior; new Judge performance requires a separately
reviewed, bound live run.
