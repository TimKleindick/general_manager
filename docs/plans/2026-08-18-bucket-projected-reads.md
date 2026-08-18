# Bucket Projected Reads Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add materialized `values()` and `values_list()` reads to every bucket type, with safe native projection for database, calculation, and raw request backends.

**Architecture:** A new internal projection engine validates fields, evaluates a private canonical-row hook, captures dependencies, and shapes tuple-backed public results. `Bucket` supplies portable manager iteration; `DatabaseBucket`, `CalculationBucket`, and `RequestBucket` override the hook with all-or-nothing native paths; `GroupBucket` delegates explicitly because it is only a virtual `Bucket` subclass.

**Tech Stack:** Python 3.12+, Django ORM, pytest/Django test utilities, Ruff, mypy, existing `CalculationRunContext` and `DependencyTracker` infrastructure.

**Spec:** `docs/specs/2026-08-18-bucket-projected-reads-design.md`

## Global Constraints

- Preserve existing bucket filtering, exclusion, ordering, slicing, grouping, pickling, equality, historical-context, and authorization-boundary behavior.
- Return shallow materialized snapshots: tuple containers, fresh `values()` row dictionaries, and ordinary un-copied attribute values.
- Require one or more explicit fields from `Interface.get_attributes()` or declared `GraphQLProperty` names.
- Use all-or-nothing native projection per call; one unsafe field sends the complete projection through portable manager iteration.
- Never create a temporary `CalculationRunContext`; cache only inside an already-active run.
- Share one canonical tuple-of-tuples cache entry across every public output mode.
- Cache at most 10,000 projected rows; larger results must still return completely.
- Keep every `GraphQLProperty`, ORM relation, unsafe custom descriptor, manager-only request bucket, and `GroupBucket` on the portable path.
- Add no dependency, database migration, setting, lazy result object, public row-limit argument, or `values_index_many()` API.
- Do not edit version numbers or the generated changelog.

---

## File Structure

### New files

- `src/general_manager/bucket/projection.py`: validation, exceptions, canonical evaluation, cache orchestration, and result shaping.
- `src/general_manager/interface/capabilities/calculation/input_resolution.py`: calculation input normalization shared by interface accessors and projection.
- `tests/unit/test_bucket_projection.py`: backend-neutral projection contract and cache behavior.

### Existing files to modify

- `src/general_manager/bucket/base_bucket.py`: concrete public methods, overloads, and portable hook.
- `src/general_manager/cache/run_context.py`: projection cache entry and accessors.
- `src/general_manager/cache/signals.py`: projection invalidation around mutations.
- `src/general_manager/bucket/group_bucket.py`: explicit grouped projection methods.
- `src/general_manager/bucket/database_bucket.py`: safe ORM projection.
- `src/general_manager/measurement/measurement_field.py`: shared stored-component reconstruction.
- `src/general_manager/interface/capabilities/calculation/lifecycle.py`: shared input resolver use.
- `src/general_manager/bucket/calculation_bucket.py`: native input projection.
- `src/general_manager/bucket/request_bucket.py`: staged raw/manager materialization and raw projection.
- `src/general_manager/public_api_registry.py`, `src/general_manager/_types/bucket.py`, `tests/snapshots/public_api_exports.json`: public exceptions.
- `tests/unit/test_base_bucket.py`, `tests/unit/test_calculation_run_context.py`, `tests/unit/test_signals.py`, `tests/unit/test_group_manager.py`, `tests/unit/test_database_bucket.py`, `tests/unit/test_measurement_field.py`, `tests/unit/test_calculation_bucket.py`, `tests/unit/test_request_interface.py`: behavior coverage.
- `docs/api/core.md`, `docs/api/cache.md`, `docs/concepts/models_entities.md`, `docs/concepts/caching.md`, `docs/howto/cache_dependent_calculation.md`: public guidance.

---

### Task 1: Shared Projection Contract and Portable Bucket Path

**Files:**
- Create: `src/general_manager/bucket/projection.py`
- Create: `tests/unit/test_bucket_projection.py`
- Modify: `src/general_manager/bucket/base_bucket.py`
- Modify: `tests/unit/test_base_bucket.py`

**Interfaces:**
- Produces: `ProjectionRows = tuple[tuple[object, ...], ...]`
- Produces: `EmptyProjectionFieldsError`, `DuplicateProjectionFieldError`, `UnknownProjectionFieldError`, `FlatProjectionFieldCountError`
- Produces: `project_values(source, fields) -> tuple[dict[str, object], ...]`
- Produces: `project_values_list(source, fields, *, flat) -> ProjectionRows | tuple[object, ...]`
- Produces: `Bucket._project_rows(fields) -> ProjectionRows`, concrete `Bucket.values()`, and overloaded `Bucket.values_list()`

- [x] **Step 1: Write failing validation and result-shape tests**

Create a focused dummy manager and bucket in `tests/unit/test_bucket_projection.py`. Cover validation order and all three shapes:

```python
def test_values_returns_tuple_of_fresh_row_dicts() -> None:
    bucket = ProjectionBucket([ProjectionRow(code="A", amount=1)])
    first = bucket.values("code", "amount")
    first[0]["amount"] = 99
    second = bucket.values("code", "amount")
    assert first == ({"code": "A", "amount": 99},)
    assert second == ({"code": "A", "amount": 1},)
    assert first[0] is not second[0]


def test_values_list_returns_tuple_rows_and_flat_tuple() -> None:
    bucket = ProjectionBucket(
        [ProjectionRow(code="A", amount=1), ProjectionRow(code="B", amount=2)]
    )
    assert bucket.values_list("code", "amount") == (("A", 1), ("B", 2))
    assert bucket.values_list("code", flat=True) == ("A", "B")
```

Use a bucket that raises on iteration to prove empty, non-string, duplicate, unknown, non-boolean `flat`, and invalid flat field-count errors occur before evaluation.

- [x] **Step 2: Run the new tests and confirm the red state**

Run: `python -m pytest tests/unit/test_bucket_projection.py -q`

Expected: import or collection failure because the projection module and methods do not exist.

- [x] **Step 3: Implement validation and non-cached shaping**

Define the exact errors and validation in `projection.py`:

```python
type ProjectionRows = tuple[tuple[object, ...], ...]


def validate_projection_fields(
    manager_class: type[GeneralManager],
    fields: tuple[object, ...],
) -> tuple[str, ...]:
    if not fields:
        raise EmptyProjectionFieldsError()
    if not all(isinstance(field, str) for field in fields):
        raise TypeError("Projection fields must be strings.")
    normalized = cast(tuple[str, ...], fields)
    if len(set(normalized)) != len(normalized):
        raise DuplicateProjectionFieldError()
    allowed = set(manager_class.Interface.get_attributes())
    allowed.update(manager_class.Interface.get_graph_ql_properties())
    unknown = tuple(field for field in normalized if field not in allowed)
    if unknown:
        raise UnknownProjectionFieldError(unknown)
    return normalized
```

Validate `type(flat) is bool` before flat field count. `project_values()` creates dictionaries with `dict(zip(fields, row, strict=True))`; flat output uses `tuple(row[0] for row in rows)`.

- [x] **Step 4: Add base methods and portable evaluation**

In `Bucket`, add `Literal`/`overload`, both public signatures, and:

```python
def _project_rows(self, fields: tuple[str, ...]) -> ProjectionRows:
    return tuple(tuple(getattr(row, field) for field in fields) for row in self)
```

The public methods delegate to the shared functions. Do not make `_project_rows()` abstract.

- [x] **Step 5: Run focused tests**

Run: `python -m pytest tests/unit/test_bucket_projection.py tests/unit/test_base_bucket.py -q`

Expected: all selected tests pass and existing custom buckets require no changes.

- [x] **Step 6: Commit**

```bash
git add src/general_manager/bucket/projection.py src/general_manager/bucket/base_bucket.py tests/unit/test_bucket_projection.py tests/unit/test_base_bucket.py
git commit -m "feat: add portable bucket projections"
```

---

### Task 2: Run-Scoped Projection Cache and Mutation Invalidation

**Files:**
- Modify: `src/general_manager/cache/run_context.py`
- Modify: `src/general_manager/cache/signals.py`
- Modify: `src/general_manager/bucket/projection.py`
- Modify: `tests/unit/test_calculation_run_context.py`
- Modify: `tests/unit/test_bucket_projection.py`
- Modify: `tests/unit/test_signals.py`

**Interfaces:**
- Consumes: `ProjectionRows`, `_bucket_index_source_signature()`, and `_project_rows()`
- Produces: `BUCKET_PROJECTION_PREFIX`, `BucketProjectionRunCacheEntry`, `get_bucket_projection_result()`, `set_bucket_projection_result()`, and `clear_bucket_projections()`

- [x] **Step 1: Write failing cache tests**

Test storage, misses, dependency replay, clearing, cross-mode reuse, field-order isolation, no-context behavior, fresh dictionaries, and the boundary:

```python
def test_projection_modes_share_one_active_run_evaluation() -> None:
    bucket = CountingProjectionBucket([ProjectionRow(code="A", amount=1)])
    with CalculationRunContext():
        assert bucket.values("code", "amount") == ({"code": "A", "amount": 1},)
        assert bucket.values_list("code", "amount") == (("A", 1),)
    assert bucket.projection_calls == 1


@pytest.mark.parametrize(("row_count", "calls"), [(10_000, 1), (10_001, 2)])
def test_projection_cache_admission_limit(row_count: int, calls: int) -> None:
    bucket = CountingProjectionBucket(make_projection_rows(row_count))
    with CalculationRunContext():
        bucket.values_list("code")
        bucket.values_list("code")
    assert bucket.projection_calls == calls
```

Add a nested `DependencyTracker` parity test for a miss and hit.

- [x] **Step 2: Confirm failures**

Run: `python -m pytest tests/unit/test_bucket_projection.py tests/unit/test_calculation_run_context.py -q`

Expected: new cache tests fail because projection context methods do not exist.

- [x] **Step 3: Implement run-context entries**

Mirror bucket-index storage:

```python
BUCKET_PROJECTION_PREFIX = "bucket_projection"


@dataclass(frozen=True)
class BucketProjectionRunCacheEntry:
    value: object
    dependencies: frozenset["Dependency"]


def _bucket_projection_cache_key(
    self, source_signature: Hashable, fields: tuple[str, ...]
) -> tuple[Hashable, ...]:
    return (BUCKET_PROJECTION_PREFIX, source_signature, fields)
```

`get_bucket_projection_result()` rejects wrong entry types and replays dependencies with `_track_validated()`. The setter freezes dependencies. Clearing discards the prefix.

- [x] **Step 4: Integrate cache orchestration**

Add `MAX_RUN_SCOPED_PROJECTION_ROWS = 10_000`. Validate, call the optional historical guard, then use `current_calculation_run_context()` without `ensure_calculation_run_context()`. Evaluate misses inside `DependencyTracker`; retain only results at or below the limit. Use `_bucket_index_source_signature()` for cache identity.

- [x] **Step 5: Add and satisfy mutation invalidation tests**

Extend pre/post mutation tests in `tests/unit/test_signals.py` to assert `clear_bucket_projections()` is called beside existing clears. Add that call at both active-context sites in `cache/signals.py`.

- [x] **Step 6: Run focused tests**

Run: `python -m pytest tests/unit/test_bucket_projection.py tests/unit/test_calculation_run_context.py tests/unit/test_signals.py -q`

Expected: all selected tests pass, including 10,000 retention, 10,001 bypass, dependency replay, and mutation clearing.

- [x] **Step 7: Commit**

```bash
git add src/general_manager/cache/run_context.py src/general_manager/cache/signals.py src/general_manager/bucket/projection.py tests/unit/test_bucket_projection.py tests/unit/test_calculation_run_context.py tests/unit/test_signals.py
git commit -m "feat: cache bucket projections per run"
```

---

### Task 3: GroupBucket Projection

**Files:**
- Modify: `src/general_manager/bucket/group_bucket.py`
- Modify: `tests/unit/test_group_manager.py`

**Interfaces:**
- Consumes: shared shaping and `ProjectionRows`
- Produces: explicit grouped public methods, `_project_rows()`, and identity source signature

- [x] **Step 1: Write failing grouped tests**

Cover keys, aggregates, sorted/sliced order, fresh dictionaries, active-run reuse, and delegated historical rejection:

```python
def test_group_bucket_projects_keys_and_aggregates(self) -> None:
    grouped = self.bucket.group_by("category")
    assert grouped.values("category", "amount") == (
        {"category": "A", "amount": 3},
        {"category": "B", "amount": 4},
    )
    assert grouped.values_list("category", flat=True) == ("A", "B")
```

- [x] **Step 2: Confirm missing-method failure**

Run: `python -m pytest tests/unit/test_group_manager.py -q`

Expected: new tests fail because virtual registration does not inherit methods.

- [x] **Step 3: Implement grouped delegation**

Add the same overloads as `Bucket`, a portable `_project_rows()` over `self`, and:

```python
def _bucket_index_source_signature(self) -> Hashable:
    return (self.__class__, self._manager_class, id(self))
```

- [x] **Step 4: Run tests and commit**

Run: `python -m pytest tests/unit/test_group_manager.py tests/unit/test_bucket_projection.py -q`

Expected: all selected tests pass.

```bash
git add src/general_manager/bucket/group_bucket.py tests/unit/test_group_manager.py
git commit -m "feat: project grouped bucket values"
```

---

### Task 4: DatabaseBucket Native Projection

**Files:**
- Modify: `src/general_manager/measurement/measurement_field.py`
- Modify: `src/general_manager/bucket/database_bucket.py`
- Modify: `tests/unit/test_measurement_field.py`
- Modify: `tests/unit/test_database_bucket.py`

**Interfaces:**
- Consumes: portable fallback and projection cache
- Produces: `MeasurementField._from_stored_components(value, unit)` and `DatabaseBucket._project_rows(fields)`

- [x] **Step 1: Extract measurement reconstruction with failing tests**

Test normal values, `None`, invalid units, and incompatible units. Move descriptor logic into:

```python
def _from_stored_components(self, value: object, unit: object) -> Measurement | None:
    if value is None or unit is None:
        return None
    try:
        magnitude = convert_magnitude(Decimal(str(value)), self.base_unit, str(unit))
    except pint.errors.PintError:
        magnitude = Decimal(str(value))
        unit = self.base_unit
    return Measurement(magnitude, str(unit))
```

Run `python -m pytest tests/unit/test_measurement_field.py -q` before and after refactoring `__get__()` to delegate. Expected: missing-helper failure, then pass.

- [x] **Step 2: Write failing native database tests**

Cover scalar and measurement fields, PK privacy, file/image strings where fixtures permit, order, slice/filter/exclude/sort preservation, historical conflicts, dependencies, equivalent-query cache reuse, and fallback:

```python
def test_native_projection_avoids_manager_construction(self) -> None:
    bucket = DatabaseBucket(User.objects.order_by("username"), UserManager)
    with patch.object(UserManager, "__init__", side_effect=AssertionError):
        assert bucket.values_list("username", flat=True) == ("alice", "bob", "carol")
```

Add relation and `GraphQLProperty` cases that spy on manager hydration and compare against ordinary iteration.

- [x] **Step 3: Confirm native assertions fail**

Run: `python -m pytest tests/unit/test_database_bucket.py -q`

Expected: manager-construction and query-count assertions fail through portable fallback.

- [x] **Step 4: Implement all-or-nothing ORM planning**

Plan only concrete non-relation fields and logical `MeasurementField` values. Reject properties, relations, collections, generic relations, and unsafe descriptors. Deduplicate concrete columns while retaining caller field order. Select `pk` plus planned columns with `values_list()` from the existing queryset.

- [x] **Step 5: Reconstruct rows and dependencies**

Call `_ensure_as_of_compatible()` and `_track_effective_dependencies()`. For each row track `{"id": primary_key}`, reconstruct measurements through the extracted helper, normalize file/image strings, and build canonical rows. If planning fails, delegate before issuing a projection query.

- [x] **Step 6: Run tests and commit**

Run: `python -m pytest tests/unit/test_measurement_field.py tests/unit/test_database_bucket.py tests/unit/test_bucket_projection.py -q`

Expected: all selected tests pass with one query and no managers for eligible projections.

```bash
git add src/general_manager/measurement/measurement_field.py src/general_manager/bucket/database_bucket.py tests/unit/test_measurement_field.py tests/unit/test_database_bucket.py
git commit -m "feat: optimize database bucket projections"
```

---

### Task 5: CalculationBucket Native Input Projection

**Files:**
- Create: `src/general_manager/interface/capabilities/calculation/input_resolution.py`
- Modify: `src/general_manager/interface/capabilities/calculation/lifecycle.py`
- Modify: `src/general_manager/bucket/calculation_bucket.py`
- Modify: `tests/unit/test_calculation_bucket.py`

**Interfaces:**
- Consumes: portable fallback and canonical projection hooks
- Produces: `resolve_calculation_input_value(interface_cls, identification, field_name, resolved_values) -> object`
- Produces: `CalculationBucket._project_rows(fields) -> ProjectionRows`

- [x] **Step 1: Write failing resolver and native-projection tests**

Cover dependent normalization, optional inputs, manager-valued dependencies, filters/excludes, allowed identifications, input sorting/reversal, historical conflicts, no manager construction, and property fallback:

```python
def test_input_only_projection_does_not_construct_managers(self, _mock_parse) -> None:
    bucket = CalculationBucket(InputCalculationManager)
    with patch.object(InputCalculationManager, "__init__", side_effect=AssertionError):
        assert bucket.values_list("region", "year") == (
            ("EU", 2025),
            ("EU", 2026),
        )


def test_mixed_property_projection_uses_portable_path(self, _mock_parse) -> None:
    bucket = CalculationBucket(InputCalculationManager)
    result = bucket.values_list("year", "computed_total")
    assert result == tuple((row.year, row.computed_total) for row in bucket)
```

Add parity between the new resolver and the current interface accessor for an input normalizer with `depends_on`.

- [x] **Step 2: Confirm failures**

Run: `python -m pytest tests/unit/test_calculation_bucket.py -q`

Expected: shared-resolver and no-manager tests fail.

- [x] **Step 3: Extract calculation input resolution**

Move recursive resolution and cached-manager tracking from the nested lifecycle closure into `input_resolution.py`:

```python
def resolve_calculation_input_value(
    interface_cls: type[CalculationInterface],
    identification: Mapping[str, object],
    field_name: str,
    resolved_values: dict[str, object],
) -> object:
    if field_name in resolved_values:
        value = resolved_values[field_name]
        track_manager_input(value)
        return value
    field = interface_cls.input_fields[field_name]
    dependencies = {
        name: resolve_calculation_input_value(
            interface_cls, identification, name, resolved_values
        )
        for name in field.depends_on
    }
    value = field.cast(
        identification.get(field_name),
        dependencies,
        cache_context=(interface_cls._parent_class, field_name),
    )
    resolved_values[field_name] = value
    return value
```

The lifecycle accessor passes its per-interface `_resolved_input_values` dictionary into this helper, preserving current caching.

- [x] **Step 4: Implement input-only projection**

If every field is in `self.input_fields`, call `generate_combinations()` once. Resolve requested inputs for each identification using one row-local `resolved_values` dictionary. If any field is not an input, delegate the complete call to `super()._project_rows(fields)`. Do not duplicate filtering or sorting logic outside `generate_combinations()`.

- [x] **Step 5: Run tests and commit**

Run: `python -m pytest tests/unit/test_calculation_bucket.py tests/unit/test_bucket_projection.py -q`

Expected: all selected tests pass; eligible projections construct no managers and properties remain portable.

```bash
git add src/general_manager/interface/capabilities/calculation/input_resolution.py src/general_manager/interface/capabilities/calculation/lifecycle.py src/general_manager/bucket/calculation_bucket.py tests/unit/test_calculation_bucket.py
git commit -m "feat: optimize calculation bucket projections"
```

---

### Task 6: Split Request Raw and Manager Materialization

**Files:**
- Modify: `src/general_manager/bucket/request_bucket.py`
- Modify: `tests/unit/test_request_interface.py`

**Interfaces:**
- Produces: `RequestBucket._ensure_raw_items() -> tuple[RequestPayload, ...]`
- Preserves: `RequestBucket._ensure_items() -> tuple[GeneralManagerType, ...]`

- [x] **Step 1: Write failing staged-materialization tests**

Prove raw materialization executes once, applies local predicates, retains count behavior, raises the existing partial-pagination error, and delays managers:

```python
def test_raw_materialization_executes_without_building_managers(self) -> None:
    bucket = RemoteProject.filter(status="active")
    with patch.object(RemoteProject, "__init__", side_effect=AssertionError):
        raw_items = bucket._ensure_raw_items()
    assert [item["id"] for item in raw_items] == [1, 2]
    assert bucket.count() == 2
```

Add an empty-result test proving `_materialized=True` distinguishes an executed empty plan.

- [x] **Step 2: Confirm the missing stage**

Run: `python -m pytest tests/unit/test_request_interface.py -q`

Expected: `_ensure_raw_items()` is missing and current evaluation constructs managers.

- [x] **Step 3: Refactor without public behavior changes**

Move execution, local predicates, pagination validation, count override, raw storage, and `_materialized=True` into `_ensure_raw_items()`. Make `_ensure_items()` call the raw stage, construct managers only when necessary, and attach payload caches with `zip(..., strict=True)`.

Constructor-supplied items/raw items, pickle restoration, slices, unions, `none()`, and `with_instances()` must retain existing behavior.

- [x] **Step 4: Run tests and commit**

Run: `python -m pytest tests/unit/test_request_interface.py -q`

Expected: all existing and new tests pass; ordinary iteration still returns managers.

```bash
git add src/general_manager/bucket/request_bucket.py tests/unit/test_request_interface.py
git commit -m "refactor: split request payload materialization"
```

---

### Task 7: RequestBucket Native Projection

**Files:**
- Modify: `src/general_manager/bucket/request_bucket.py`
- Modify: `tests/unit/test_request_interface.py`

**Interfaces:**
- Consumes: `_ensure_raw_items()` and portable fallback
- Produces: `RequestBucket._project_rows(fields) -> ProjectionRows`

- [x] **Step 1: Write failing native request tests**

Cover inputs, source paths, defaults, required-field errors, normalizers, local predicates, lazy execution once, identification dependencies, unsupported historical reads, manager-only fallback, and property fallback:

```python
def test_request_projection_uses_raw_payload_without_managers(self) -> None:
    bucket = RemoteProject.filter(status="active")
    with patch.object(RemoteProject, "__init__", side_effect=AssertionError):
        result = bucket.values_list("id", "display_name")
    assert result == ((1, "ALPHA"), (2, "BETA"))


def test_materialized_request_subset_uses_portable_projection(self) -> None:
    source = RemoteProject.filter(status="active")
    items = tuple(source)
    subset = source.with_instances(items[:1])
    assert subset.values("name") == ({"name": items[0].name},)
```

Compare dependency sets from native and portable evaluation.

- [x] **Step 2: Confirm native assertions fail**

Run: `python -m pytest tests/unit/test_request_interface.py -q`

Expected: no-manager assertion fails through portable projection.

- [x] **Step 3: Implement raw-payload projection**

Use native projection only when every field is in `input_fields` or declared request `fields`, and the bucket has a request plan or raw snapshot. Manager-only buckets delegate completely.

For each payload, extract identification once, track it, then resolve:

```python
if field_name in self._interface_cls.input_fields:
    value = identification[field_name]
else:
    value = self._interface_cls.resolve_payload_value(payload, field_name)
```

Call `ensure_as_of_read_supported()` before raw materialization. Do not rerun an executed empty request.

- [x] **Step 4: Run tests and commit**

Run: `python -m pytest tests/unit/test_request_interface.py tests/unit/test_bucket_projection.py tests/unit/test_calculation_run_context.py -q`

Expected: all selected tests pass with one request, no managers for eligible fields, and portable subsets/properties.

```bash
git add src/general_manager/bucket/request_bucket.py tests/unit/test_request_interface.py
git commit -m "feat: optimize request bucket projections"
```

---

### Task 8: Public Exports and Documentation

**Files:**
- Modify: `src/general_manager/public_api_registry.py`
- Generate: `src/general_manager/_types/bucket.py`
- Generate: `tests/snapshots/public_api_exports.json`
- Modify: `docs/api/core.md`
- Modify: `docs/api/cache.md`
- Modify: `docs/concepts/models_entities.md`
- Modify: `docs/concepts/caching.md`
- Modify: `docs/howto/cache_dependent_calculation.md`

**Interfaces:**
- Produces: stable `general_manager.bucket` imports for the four projection errors

- [x] **Step 1: Add registry entries and prove the snapshot is stale**

Add all four exception targets to `BUCKET_EXPORTS` and run:

`python -m pytest tests/unit/test_public_api_init_modules.py -q`

Expected: snapshot-registry mismatch.

- [x] **Step 2: Regenerate type modules and snapshot**

Run: `python scripts/generate_public_api_types.py`

Inspect the diff and retain only generator changes corresponding to the four bucket exceptions.

- [x] **Step 3: Document exact behavior**

Document signatures, tuple/dict shapes, explicit field namespace, validation order, shallow snapshots, native/portable selection, historical behavior, and authorization boundary in `docs/api/core.md`.

Document active-run-only reuse, shared canonical rows, 10,000-row retention, dependency replay, mutation clearing, and byte-budget interaction in cache docs.

Add this how-to example and explain that computed properties force whole-call fallback:

```python
rows = DerivativeVolume.filter(derivative=self.derivative)
daily_values = rows.values("volume_date", "quantity")
daily_dates = rows.values_list("volume_date", flat=True)
```

- [x] **Step 4: Run export/docs tests**

Run: `python -m pytest tests/unit/test_public_api_init_modules.py tests/unit/test_generate_public_api_types.py tests/docs/test_public_api_docs_coverage.py -q`

Expected: registry, snapshot, lazy exports, generated types, and docs coverage pass.

- [x] **Step 5: Re-run focused bucket matrix**

Run: `python -m pytest tests/unit/test_bucket_projection.py tests/unit/test_base_bucket.py tests/unit/test_database_bucket.py tests/unit/test_calculation_bucket.py tests/unit/test_request_interface.py tests/unit/test_group_manager.py -q`

Expected: all selected tests pass against the documented contract.

- [x] **Step 6: Commit**

```bash
git add src/general_manager/public_api_registry.py src/general_manager/_types/bucket.py tests/snapshots/public_api_exports.json docs/api/core.md docs/api/cache.md docs/concepts/models_entities.md docs/concepts/caching.md docs/howto/cache_dependent_calculation.md
git commit -m "docs: publish bucket projection API"
```

---

### Task 9: Full Verification and Review Gate

**Files:**
- Verify only; modify files only for failures attributable to this feature.

**Interfaces:**
- Consumes: all prior tasks
- Produces: evidence that the complete implementation meets the spec

- [x] **Step 1: Run the focused matrix**

```bash
python -m pytest \
  tests/unit/test_bucket_projection.py \
  tests/unit/test_base_bucket.py \
  tests/unit/test_calculation_run_context.py \
  tests/unit/test_signals.py \
  tests/unit/test_measurement_field.py \
  tests/unit/test_database_bucket.py \
  tests/unit/test_calculation_bucket.py \
  tests/unit/test_request_interface.py \
  tests/unit/test_group_manager.py \
  tests/unit/test_public_api_init_modules.py \
  tests/docs/test_public_api_docs_coverage.py \
  -q
```

Expected: zero failures.

- [x] **Step 2: Run formatting and lint**

```bash
ruff format --check src/general_manager tests
ruff check src/general_manager tests
```

Expected: both commands exit zero without changes.

- [x] **Step 3: Run strict typing**

Run: `mypy src/general_manager`

Expected: zero type errors, including both overload branches.

- [x] **Step 4: Run the full suite and hooks**

```bash
python -m pytest -q
pre-commit run --all-files
```

Expected: zero test failures and every hook passes.

- [x] **Step 5: Audit final scope**

```bash
git status --short
git diff --check
git log --oneline --decorate -10
```

Confirm there is no dependency, migration, setting, version, changelog, lazy-result, partial-optimization, implicit-all-fields, public row-limit, or `values_index_many()` change.

- [x] **Step 6: Request code review**

Use `superpowers:requesting-code-review` against the complete implementation. Address only verified findings, rerun affected focused tests after corrections, and repeat Steps 1-5 before completion.
