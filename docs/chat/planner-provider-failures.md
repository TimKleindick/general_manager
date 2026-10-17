# Planner provider failures

If all three existing planning attempts raise an exception from the
provider stream, planned chat returns the existing public `provider_failed` error
and audits `reason_origin: provider_exception`. The error contains no provider
exception text. This separates an unavailable planner response from a response
that was received and rejected as an invalid plan.

Malformed provider streams, invalid deadline configuration, request preparation
failures and rejected plan content retain the existing `invalid_plan` result.
If any attempt follows one of those paths, a terminal mixed failure also retains
`invalid_plan`. A later valid response still succeeds normally.

The shared round adapter identifies stream exceptions only when explicitly
called with `classify_provider_errors=True`, as the planner does. Its default
is `False`, preserving original exception types for other Python callers.
Compaction and native response validation stay outside the stream exception
boundary. Cancellation and the adapter's own deadline are not wrapped.

This changes failure attribution only. The planner still uses the same primary,
correction and fallback attempts, timeout values and usage accounting.
Cancellation still propagates. A transport STOP continues to prevent subsequent
network requests; this change does not clear it, retry a request or increase a
deadline. Frozen evaluation results retain their original error and score.
