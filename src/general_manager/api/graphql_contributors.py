"""Authorization of the values that contribute to ordinary GraphQL lists.

Search retains its stricter projection contract in graphql_query_permissions.
Here a denied scalar makes its row unavailable; a denied related child is
removed from that predicate's witness domain before applying native predicates.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from inspect import getattr_static
from typing import TYPE_CHECKING, cast

from django.db import models
from django.db.models import FilteredRelation, Q
from graphql import GraphQLError

from general_manager.api.graphql_query_permissions import (
    INCOMPLETE_ERROR,
    QueryFieldAuthorizationBudget,
    QueryFieldPermissionError,
    _LOOKUPS,
    _NativeQueryStructures,
    _operand_field_names,
    _prepare_pushdown_structure,
    _relation_suffix_is_lookup,
)
from general_manager.api.graphql_relations import (
    get_graphql_manager_registry,
    resolve_general_manager_type,
)
from general_manager.bucket.base_bucket import (
    Bucket,
    _DEFAULT_GROUPING_CONTRACT as _BASE_GROUPING_CONTRACT,
)
from general_manager.bucket.calculation_bucket import (
    _DEFAULT_GROUPING_CONTRACT as _CALCULATION_GROUPING_CONTRACT,
)
from general_manager.bucket.database_bucket import (
    DatabaseBucket,
    _DEFAULT_GROUPING_CONTRACT as _DATABASE_GROUPING_CONTRACT,
    _DatabaseMaterializedBucket,
    _database_primary_key_for,
)
from general_manager.bucket.request_bucket import RequestBucket
from general_manager.bucket.indexing import freeze_bucket_index_value
from general_manager.manager.general_manager import GeneralManager
from general_manager.permission.base_permission import ReadPermissionPlan
from general_manager.permission.manager_based_permission import (
    _can_prepare_local_read_definitions,
    additional_local_read_permission,
    additional_resolved_read_permission,
    declared_field_read_plan,
)
from general_manager.utils.executable_contract import executable_matches

if TYPE_CHECKING:
    from graphene import ResolveInfo
    from general_manager.api.graphql_resolvers import QueryParameterPlan
    from general_manager.manager.group_manager import GroupManager
    from general_manager.bucket.calculation_bucket import CalculationBucket
    from general_manager.utils.filter_parser import ParsedFilters
    from general_manager.api.graphql_query_permissions import _NativeQueryStructure

type Domains = dict[str, set[object]]


_DEFAULT_GROUPING_METHODS = (
    _BASE_GROUPING_CONTRACT,
    _DATABASE_GROUPING_CONTRACT,
    _CALCULATION_GROUPING_CONTRACT,
)


def _has_zero_key_grouping_default(queryset: Bucket[GeneralManager]) -> bool:
    """Recognize only the unchanged built-in no-argument grouping contract."""
    if (
        getattr_static(type(queryset), "__getattribute__")
        is not object.__getattribute__
    ):
        return False
    method = getattr_static(queryset, "group_by", None)
    base, base_signature = _DEFAULT_GROUPING_METHODS[0]
    return (
        Bucket.group_by is base
        and executable_matches(base, base_signature)
        and any(
            method is original and executable_matches(original, signature)
            for original, signature in _DEFAULT_GROUPING_METHODS
        )
    )


def _relation_metadata(
    manager: type[GeneralManager],
    source: str,
    attributes: Mapping[str, Mapping[str, object]],
) -> Mapping[str, object]:
    """Keep property relation hints on the runtime, permission-gated path."""
    if source in attributes:
        return attributes[source]
    prop = manager.Interface.get_graph_ql_properties().get(source)
    if prop is None:
        return {}
    from general_manager.api.graphql_resolvers import _is_collection_annotation

    annotation = prop.graphql_type_hint
    return {
        "type": annotation,
        "relation_kind": "collection"
        if _is_collection_annotation(annotation)
        else "direct",
    }


def _contributor_identity(instance: GeneralManager) -> object:
    model = getattr(instance.Interface, "_model", None)
    if isinstance(model, type) and issubclass(model, models.Model):
        return _database_primary_key_for(instance)
    return freeze_bucket_index_value(instance.identification)


class ContributorAuthorization:
    """One operation's definitions and accounting, without cached row grants."""

    def __init__(
        self,
        info: ResolveInfo,
        calculation_plan: CalculationBucket[GeneralManager] | None = None,
    ) -> None:
        self.info = info
        self.budget = QueryFieldAuthorizationBudget()
        self.plans: dict[
            tuple[type[GeneralManager], str], ReadPermissionPlan | None
        ] = {}
        self.additional: dict[tuple[type[GeneralManager], str], bool | None] = {}
        self.structures = _NativeQueryStructures(allow_calculation=True)
        self.prepared: dict[type[GeneralManager], _NativeQueryStructure | None] = {}
        self.calculation_plan = calculation_plan

    def canonical_authority(self) -> bool:
        from general_manager.api import graphql_resolvers

        return all(
            current is original and executable_matches(original, signature)
            for current, (original, signature) in zip(
                (
                    graphql_resolvers.check_read_permission,
                    graphql_resolvers.check_read_permission_for_user,
                    graphql_resolvers.can_read_instance,
                    graphql_resolvers.can_read_instance_for_user,
                ),
                (
                    *graphql_resolvers._QUERY_PUSHDOWN_CHECKS,
                    *graphql_resolvers._CONTRIBUTOR_ROW_CHECKS,
                ),
                strict=True,
            )
        )

    def after_callback(self) -> None:
        # Once a provider is unknown, continuing its runtime path remains
        # safe even if callbacks later replace it with a plannable provider.
        # Only definitive/conditional actor plans need to be discarded.
        self.plans = {key: None for key, value in self.plans.items() if value is None}
        self.additional.clear()
        self.prepared.clear()

    def structure(self, manager: type[GeneralManager]) -> _NativeQueryStructure | None:
        """Reuse this operation's complete native construction/schema proof."""
        if manager not in self.prepared:
            self.prepared[manager] = _prepare_pushdown_structure(manager)
        return self.prepared[manager]

    def plan(
        self, manager: type[GeneralManager], name: str
    ) -> ReadPermissionPlan | None:
        key = manager, name
        if key in self.plans and self.plans[key] is None:
            # Unknown stays runtime even if declarations or authorities change.
            return None
        if not self.canonical_authority():
            return None
        if key not in self.plans:
            plan = declared_field_read_plan(manager, name, self.info.context.user)
            self.plans[key] = (
                plan if plan is not None and not plan.requires_instance_check else None
            )
            if self.plans[key] is not None:
                self.additional[key] = additional_local_read_permission(
                    getattr_static(manager, "Permission", None), name, manager=manager
                )
        return self.plans[key]

    def field_allowed(
        self, instance: GeneralManager, name: str, *, charged: list[bool] | None = None
    ) -> bool:
        from general_manager.api.graphql_resolvers import check_read_permission

        structure = self.structures.for_instance(instance)
        key = type(instance), name
        known_runtime_plan = (
            structure is not None and key in self.plans and self.plans[key] is None
        )
        plan = self.plan(type(instance), name) if structure is not None else None
        if plan is not None and not plan.requires_instance_check:
            if plan.decision == "allow_all":
                return True
            if self.additional[type(instance), name] is False:
                return True
            if plan.decision == "deny_all":
                return False
        try:

            def charge() -> None:
                if charged is None or not charged[0]:
                    self.budget.charge()
                    if charged is not None:
                        charged[0] = True

            # A previously unknown plan returns without providers. Only then
            # can the just-observed instance policy replace another inspection.
            permission = (
                structure.permission
                if structure is not None and known_runtime_plan
                else getattr_static(instance, "Permission", None)
            )
            if getattr_static(
                permission, "__based_on__", None
            ) is not None and _can_prepare_local_read_definitions(permission):
                from general_manager.permission.base_permission import BasePermission

                def charge_resolved(resolved: BasePermission) -> None:
                    if additional_resolved_read_permission(resolved, name) is not False:
                        charge()

                return check_read_permission(
                    instance, self.info, name, before_check=charge_resolved
                )
            additional = (
                structure.additional_for(name)
                if structure is not None
                else additional_local_read_permission(
                    permission, name, manager=type(instance)
                )
            )
            if additional is not False:
                charge()
            return check_read_permission(instance, self.info, name)
        finally:
            # Runtime extension callbacks can change rules, registry methods or
            # actor attributes. Later decisions must observe those changes.
            self.after_callback()

    def row_allowed(self, instance: GeneralManager) -> bool:
        from general_manager.api.graphql_resolvers import can_read_instance

        structure = self.structures.for_instance(instance)
        plan = self.plan(type(instance), "") if structure is not None else None
        if plan is not None and not plan.requires_instance_check:
            if plan.decision == "allow_all":
                return True
            if plan.decision == "deny_all":
                return False
        try:
            return can_read_instance(instance, self.info)
        finally:
            self.after_callback()

    def ordinary_path(self, manager: type[GeneralManager], path: str) -> bool:
        """Certify passive native paths with definitive field and target grants."""
        structure = self.structure(manager)
        if structure is None:
            return False
        parts = path.split("__")
        field, rest = parts[0], parts[1:]
        aliases, names, _ = _operand_field_names(
            manager, field, structure.attributes, native_paths=False
        )
        if len(aliases) > 1 or not names.intersection(structure.attributes):
            return False
        for name in names:
            plan = self.plan(manager, name)
            covered = (
                plan is not None
                and not plan.requires_instance_check
                and self.additional[manager, name] is False
            )
            if not covered and (
                plan is None
                or plan.decision != "allow_all"
                or plan.requires_instance_check
            ):
                return False
        from general_manager.api.graphql_resolvers import (
            relation_id_alias_field,
            relation_id_alias_target_fields,
        )

        source = relation_id_alias_field(manager, field) or (
            aliases[0] if aliases else field
        )
        metadata: Mapping[str, object] = structure.attributes.get(source, {})
        target = resolve_general_manager_type(
            metadata.get("type"), get_graphql_manager_registry()
        )
        if target is None:
            return not rest or (len(rest) == 1 and rest[0] in _LOOKUPS)
        plan = self.plan(target, "")
        if plan is None or plan.decision != "allow_all" or plan.requires_instance_check:
            return False
        target_structure = self.structure(target)
        if target_structure is None:
            return False
        if not rest or _relation_suffix_is_lookup(
            rest, target_structure.attributes.keys() | target_structure.properties
        ):
            return all(
                self.ordinary_path(target, leaf)
                for leaf in relation_id_alias_target_fields(structure.interface, source)
            )
        return self.ordinary_path(target, "__".join(rest))

    def path(self, instance: GeneralManager, path: str) -> tuple[bool, Domains]:
        """Return availability and authorized plural domains for one operand."""
        return self._walk(instance, tuple(path.split("__")), "")

    def _walk(
        self, instance: GeneralManager, parts: tuple[str, ...], prefix: str
    ) -> tuple[bool, Domains]:
        from general_manager.api.graphql_resolvers import (
            relation_id_alias_field,
            relation_id_alias_target_fields,
        )

        field, *rest = parts
        structure = self.structures.for_instance(instance)
        prepared = (
            structure.flat_field(parts)
            if structure is not None
            and not structure.calculation
            and not field.endswith("_id")
            else None
        )
        if prepared is not None:
            # Keep each object's validation and runtime field decisions; only
            # passive scalar spelling/type resolution belongs to the structure.
            set(instance.identification)
            charged = [False]
            ordered_names = (
                (field, *sorted(name for name in prepared.names if name != field))
                if field in prepared.names
                else tuple(sorted(prepared.names))
            )
            for name in ordered_names:
                if not self.field_allowed(instance, name, charged=charged):
                    return False, {}
            return True, {}
        attributes = (
            structure.attributes
            if structure is not None
            else instance.Interface.get_attribute_types()
        )
        operands = (
            structure.calculation_operands(field) if structure is not None else None
        )
        if (
            structure is not None
            and not structure.calculation
            and field in attributes
            and field not in structure.properties
            and not field.endswith("_id")
        ):
            if field not in structure.operand_shapes:
                resolved_aliases, resolved_names, resolved_relations = (
                    _operand_field_names(
                        type(instance), field, attributes, native_paths=False
                    )
                )
                structure.operand_shapes[field] = (
                    tuple(resolved_aliases),
                    frozenset(resolved_names),
                    frozenset(resolved_relations),
                )
            operands = structure.operand_shapes[field]
        aliases, names, _ = (
            operands
            if operands is not None
            else _operand_field_names(
                type(instance), field, attributes, native_paths=False
            )
        )
        known = set(attributes) | set(instance.identification)
        known.update(
            structure.properties
            if structure is not None
            else instance.Interface.get_graph_ql_properties()
        )
        known.update(
            structure.attribute_names
            if structure is not None
            else instance.Interface.get_attributes()
        )
        known.update(instance.Interface.input_fields)
        if not names.intersection(known):
            raise QueryFieldPermissionError(field)
        charged = [False]
        ordered_names = (
            (field, *sorted(names - {field}))
            if field in names
            else tuple(sorted(names))
        )
        for name in ordered_names:
            if not self.field_allowed(instance, name, charged=charged):
                return False, {}
        source = aliases[0] if aliases else field
        alias = relation_id_alias_field(type(instance), field)
        if alias is not None:
            source = alias
        metadata = _relation_metadata(type(instance), source, attributes)
        target = resolve_general_manager_type(
            metadata.get("type"), get_graphql_manager_registry()
        )
        if target is None:
            if rest and (len(rest) != 1 or rest[0] not in _LOOKUPS):
                raise QueryFieldPermissionError("__".join(parts))
            return True, {}
        if rest and len(rest) == 1 and rest[0] in _LOOKUPS:
            target_fields = set(target.Interface.get_attribute_types())
            target_fields.update(target.Interface.get_graph_ql_properties())
            if _relation_suffix_is_lookup(rest, target_fields):
                rest = []
        value = getattr(instance, source)
        if value is None:
            return True, {}
        plural = isinstance(value, (Bucket, list, tuple, set))
        members = value if plural else (value,)
        sql_path = f"{prefix}__{field}" if prefix else field
        domains: Domains = {sql_path: set()} if plural else {}
        for member in cast(Iterable[object], members):
            if not isinstance(member, GeneralManager):
                message = "Cannot authorize ambiguous query relation."
                raise GraphQLError(message)
            if not self.row_allowed(member):
                if not plural:
                    return False, {}
                continue
            if rest:
                allowed, nested = self._walk(member, tuple(rest), sql_path)
            else:
                leaves = (
                    relation_id_alias_target_fields(instance.Interface, source)
                    if alias
                    or (
                        source in attributes
                        and metadata.get("relation_kind") == "direct"
                    )
                    else tuple(member.identification)
                )
                allowed, nested = True, {}
                for leaf in leaves:
                    if not self.field_allowed(member, leaf):
                        allowed = False
                        break
            if not allowed:
                if not plural:
                    return False, {}
                continue
            if plural:
                domains[sql_path].add(_contributor_identity(member))
            for key, ids in nested.items():
                domains.setdefault(key, set()).update(ids)
        if isinstance(value, RequestBucket) and not value.response_is_complete:
            raise GraphQLError(INCOMPLETE_ERROR)
        return True, domains


def _merge_domains(target: Domains, additions: Domains) -> None:
    for path, ids in additions.items():
        if path in target:
            target[path].intersection_update(ids)
        else:
            target[path] = set(ids)


def _witness_filter(
    source: DatabaseBucket[GeneralManager],
    domains: Mapping[str, object],
    predicates: Mapping[str, object],
) -> DatabaseBucket[GeneralManager]:
    """Use native joins/lookups, with authorized children inside each witness."""
    source._track_effective_dependencies()
    query = source._data
    aliases: dict[str, str] = {}

    def rewrite(path: str) -> str:
        for prefix in sorted(aliases, key=len, reverse=True):
            if path == prefix or path.startswith(prefix + "__"):
                return aliases[prefix] + path[len(prefix) :]
        return path

    for index, path in enumerate(sorted(domains, key=lambda value: value.count("__"))):
        relation = rewrite(path)
        alias = f"_gm_contributor_{index}"
        while alias in query.query.annotations or alias in getattr(
            query.query, "_filtered_relations", {}
        ):
            alias += "_"
        allowed = domains[path]
        if isinstance(allowed, models.QuerySet):
            allowed = models.Subquery(allowed)
        query = query.annotate(
            **{
                alias: FilteredRelation(
                    relation, condition=Q(**{relation + "__pk__in": allowed})
                )
            }
        )
        aliases[path] = alias
    filtered = DatabaseBucket(
        query,
        source._manager_class,
        source.filters,
        source.excludes,
        search_date=source._search_date,
        run_scoped_cacheable=source._run_scoped_cacheable,
    ).filter(**{rewrite(key): value for key, value in predicates.items()})
    filtered._sort_fields = source._sort_fields
    filtered._track_effective_dependencies()
    return filtered


def _filtered_query(
    source: DatabaseBucket[GeneralManager],
    identity: object,
    domains: Domains,
    predicates: Mapping[str, object],
) -> models.QuerySet[models.Model]:
    return _witness_filter(source.filter(pk=identity), domains, predicates)._data


class _NativeContributors:
    """Compile exact field/related-row plans without visiting native values."""

    def __init__(
        self,
        source: DatabaseBucket[GeneralManager],
        authorizer: ContributorAuthorization,
    ) -> None:
        self.source = source
        self.authorizer = authorizer

    def scope(
        self, manager: type[GeneralManager], plan: ReadPermissionPlan
    ) -> DatabaseBucket[GeneralManager] | None:
        from general_manager.api.graphql_resolvers import prepare_read_authorization

        if plan.requires_instance_check:
            return None
        scope = manager.filter(search_date=self.source._search_date)
        if type(scope) is not DatabaseBucket:
            return None
        scope._data = scope._data.using(self.source._data.db)
        prepared = prepare_read_authorization(
            scope, manager, self.authorizer.info, permission_plan=plan
        ).queryset
        if type(prepared) is not DatabaseBucket:
            return None
        # These subqueries contribute related metadata/permission facts to the
        # result even though Django executes them inside the root SQL query.
        prepared._track_effective_dependencies()
        return prepared

    def available(
        self,
        manager: type[GeneralManager],
        path: str,
        domains: dict[str, object] | None = None,
        prefix: str = "",
    ) -> Q | None:
        """Availability is separate from predicate truth, including null FKs."""
        structure = self.authorizer.structure(manager)
        if structure is None:
            return None
        field, *rest = path.split("__")
        aliases, names, _relations = _operand_field_names(
            manager, field, structure.attributes, native_paths=False
        )
        if len(aliases) > 1 or not names.intersection(structure.attributes):
            return None
        constraints = Q()
        for name in names:
            plan = self.authorizer.plan(manager, name)
            if plan is None or plan.requires_instance_check:
                return None
            if self.authorizer.additional[manager, name] is False:
                # The root or related scope has already applied its row plan.
                continue
            if plan.decision == "allow_all":
                continue
            allowed = self.scope(manager, plan)
            if allowed is None:
                return None
            constraints &= Q(pk__in=allowed._data.values("pk"))
        from general_manager.api.graphql_resolvers import (
            relation_id_alias_field,
            relation_id_alias_target_fields,
        )

        source = relation_id_alias_field(manager, field) or (
            aliases[0] if aliases else field
        )
        metadata: Mapping[str, object] = structure.attributes.get(source, {})
        plural = metadata.get("relation_kind") == "collection"
        if plural and domains is None:
            return None
        target = resolve_general_manager_type(
            metadata.get("type"), get_graphql_manager_registry()
        )
        if target is None:
            return (
                constraints
                if not rest or (len(rest) == 1 and rest[0] in _LOOKUPS)
                else None
            )
        target_structure = self.authorizer.structure(target)
        if target_structure is None:
            return None
        row_plan = self.authorizer.plan(target, "")
        if row_plan is None:
            return None
        target_scope = self.scope(target, row_plan)
        if target_scope is None:
            return None
        if _relation_suffix_is_lookup(
            rest, target_structure.attributes.keys() | target_structure.properties
        ):
            rest = []
        leaves = (
            ("__".join(rest),)
            if rest
            else relation_id_alias_target_fields(structure.interface, source)
        )
        relation = str(metadata.get("filter_lookup", source))
        sql_path = f"{prefix}__{relation}" if prefix else relation
        for leaf in leaves:
            nested = self.available(target, leaf, domains, sql_path)
            if nested is None:
                return None
            target_scope._data = target_scope._data.filter(nested)
            target_scope._set_trusted_query_signature(None)
        if plural:
            assert domains is not None
            if sql_path in domains:
                target_scope._data = target_scope._data.filter(pk__in=domains[sql_path])
                target_scope._set_trusted_query_signature(None)
            domains[sql_path] = target_scope._data.values("pk")
            return constraints
        constraints &= Q(**{f"{relation}__isnull": True}) | Q(
            **{f"{relation}__pk__in": target_scope._data.values("pk")}
        )
        return constraints

    def apply(self, paths: Iterable[str]) -> DatabaseBucket[GeneralManager] | None:
        constraints = Q()
        for path in sorted(paths):
            current = self.available(self.source._manager_class, path)
            if current is None:
                return None
            constraints &= current
        # Keep the bucket's dependency, historical and source-bag ownership.
        result = self.source.all()
        result._data = result._data.filter(constraints)
        result._set_trusted_query_signature(None)
        return result

    def predicates(
        self, plan: QueryParameterPlan, ordering_paths: Iterable[str]
    ) -> DatabaseBucket[GeneralManager] | None:
        """Keep each grouped negative lookup's own readable witness domain."""
        ordering_paths = tuple(ordering_paths)
        if any(
            _plural_path(self.source._manager_class, path) for path in ordering_paths
        ):
            return None
        constraints = Q()
        positive: dict[str, object] = {}
        for path in sorted(set(plan.filters) | set(ordering_paths)):
            available = self.available(self.source._manager_class, path, positive)
            if available is None:
                return None
            constraints &= available
        negatives = []
        for mapping in (plan.excludes, plan.normalized_excludes):
            group = []
            for path, value in mapping.items():
                domains: dict[str, object] = {}
                available = self.available(self.source._manager_class, path, domains)
                if available is None:
                    return None
                constraints &= available
                group.append((path, value, domains))
            negatives.append(group)
        result = self.source.all()
        result._data = result._data.filter(constraints)
        result._set_trusted_query_signature(None)
        result = _witness_filter(result, positive, plan.filters)
        for group in negatives:
            if not group:
                continue
            matches = result._data
            for path, value, domains in group:
                witness = _witness_filter(result, domains, {path: value})
                matches = matches.filter(pk__in=witness._data.values("pk"))
            # Native subqueries are SQL provenance, not serializable lookup
            # values for dependency keys. Root and related scopes were tracked
            # above; retain their ownership and reset the trusted SQL shortcut.
            result._data = result._data.exclude(
                pk__in=models.Subquery(matches.values("pk"))
            )
            result._set_trusted_query_signature(None)
        return result


def filter_contributors(
    candidates: Bucket[GeneralManager],
    info: ResolveInfo,
    paths: Iterable[str],
    *,
    rows_authorized: bool = False,
) -> Bucket[GeneralManager]:
    """Exclude unavailable scalar/FK contributors without changing search."""
    authorizer = ContributorAuthorization(info)
    paths = tuple(sorted(set(paths)))
    if not paths and rows_authorized:
        return candidates
    row_plan = authorizer.plan(candidates._manager_class, "")
    all_rows_visible = rows_authorized or (
        row_plan is not None
        and row_plan.decision == "allow_all"
        and not row_plan.requires_instance_check
    )
    if (
        all_rows_visible
        and type(candidates) is DatabaseBucket
        and all(
            authorizer.ordinary_path(candidates._manager_class, path) for path in paths
        )
    ):
        return candidates
    kept = []
    for instance in candidates:
        if (rows_authorized or authorizer.row_allowed(instance)) and all(
            authorizer.path(instance, path)[0] for path in paths
        ):
            kept.append(instance)
    if isinstance(candidates, RequestBucket) and not candidates.response_is_complete:
        raise GraphQLError(INCOMPLETE_ERROR)
    return candidates.with_instances(kept)


def apply_list_contributors(
    candidates: Bucket[GeneralManager],
    info: ResolveInfo,
    plan: QueryParameterPlan,
    ordering_paths: Iterable[str],
    *,
    calculation_plan: CalculationBucket[GeneralManager] | None = None,
) -> Bucket[GeneralManager]:
    """Apply predicates over authorized witnesses, retaining native bag behavior."""
    from general_manager.api.graphql_resolvers import apply_query_parameter_plan

    paths = set(plan.filters) | set(plan.excludes) | set(plan.normalized_excludes)
    paths.update(ordering_paths)
    authorizer = ContributorAuthorization(info, calculation_plan)
    visible: Bucket[GeneralManager]
    if isinstance(candidates, DatabaseBucket):
        validation_source = candidates
    elif isinstance(candidates, _DatabaseMaterializedBucket):
        validation_source = candidates._lookup_bucket()
    else:
        validation_source = None
    if validation_source is not None:
        for predicates in (plan.filters, plan.excludes, plan.normalized_excludes):
            validation_source._validate_filter_structure(**predicates)
    if type(candidates) is DatabaseBucket and all(
        authorizer.ordinary_path(candidates._manager_class, path) for path in paths
    ):
        return apply_query_parameter_plan(candidates, plan, None)
    if isinstance(candidates, (DatabaseBucket, _DatabaseMaterializedBucket)) and all(
        native_scalar_path(candidates._manager_class, path) for path in paths
    ):
        if type(candidates) is DatabaseBucket:
            native = _NativeContributors(candidates, authorizer).apply(paths)
            if native is not None:
                return apply_query_parameter_plan(native, plan, None)
        # No plural witness or Python getter is involved. Scan only for the
        # remaining ACLs, then retain backend count/order/page and fresh DB data.
        kept = []
        for instance in candidates:
            if all(authorizer.path(instance, path)[0] for path in sorted(paths)):
                kept.append(instance)
        if isinstance(candidates, DatabaseBucket):
            visible = candidates.filter(
                pk__in=[_database_primary_key_for(item) for item in kept]
            )
        else:
            visible = candidates.with_instances(kept)
        return apply_query_parameter_plan(visible, plan, None)
    if type(candidates) is DatabaseBucket:
        native = _NativeContributors(candidates, authorizer).predicates(
            plan, ordering_paths
        )
        if native is not None:
            return native
    if not isinstance(candidates, (DatabaseBucket, _DatabaseMaterializedBucket)):
        if not any(_plural_path(candidates._manager_class, path) for path in paths):
            visible = filter_contributors(candidates, info, paths, rows_authorized=True)
            return apply_query_parameter_plan(visible, plan, None)
        kept = []
        parsed_filters = (
            calculation_plan._parsed_filter_call_groups[0]
            if calculation_plan is not None
            and calculation_plan._parsed_filter_call_groups
            else None
        )
        parsed_negatives = iter(
            calculation_plan._parsed_exclude_call_groups
            if calculation_plan is not None
            else ()
        )
        negative_plans = [
            (group, next(parsed_negatives, None) if group else None)
            for group in (plan.excludes, plan.normalized_excludes)
        ]
        for instance in candidates:
            available, matched = _generic_match(
                instance, plan.filters, authorizer, parsed_filters
            )
            if not available or not matched:
                continue
            denied = False
            for group, parsed_group in negative_plans:
                outcomes = [
                    _generic_match(instance, {key: value}, authorizer, parsed_group)
                    for key, value in group.items()
                ]
                if any(not allowed for allowed, _match in outcomes):
                    available = False
                    break
                if outcomes and all(match for _allowed, match in outcomes):
                    denied = True
                    break
            if (
                available
                and not denied
                and all(authorizer.path(instance, path)[0] for path in ordering_paths)
            ):
                kept.append(instance)
        if (
            isinstance(candidates, RequestBucket)
            and not candidates.response_is_complete
        ):
            raise GraphQLError(INCOMPLETE_ERROR)
        return candidates.with_instances(kept)
    # This owner preserves annotations/snapshot/database routing while removing
    # original membership joins. Their bag is already present in candidates.
    exact = candidates.with_instances(())
    assert isinstance(exact, _DatabaseMaterializedBucket)
    lookup_source = exact._lookup_bucket()
    kept = []
    properties = candidates._manager_class.Interface.get_graph_ql_properties()
    for instance in candidates:
        available = True
        positive: Domains = {}
        negatives: list[list[tuple[str, object, Domains]]] = []
        for path in sorted(set(plan.filters) | set(ordering_paths)):
            allowed, domains = authorizer.path(instance, path)
            if not allowed:
                available = False
                break
            _merge_domains(
                positive,
                {
                    key: ids
                    for key, ids in domains.items()
                    if key.split("__", 1)[0] not in properties
                    or properties[key.split("__", 1)[0]].query_annotation is not None
                },
            )
        if not available:
            continue
        for mapping in (plan.excludes, plan.normalized_excludes):
            negative_clause = []
            for path, value in mapping.items():
                allowed, domains = authorizer.path(instance, path)
                if not allowed:
                    available = False
                    break
                negative_clause.append((path, value, domains))
            negatives.append(negative_clause)
        if not available:
            continue
        python_filters = {
            key: value
            for key, value in plan.filters.items()
            if key.split("__", 1)[0] in properties
            and properties[key.split("__", 1)[0]].query_annotation is None
        }
        native_filters = {
            key: value
            for key, value in plan.filters.items()
            if key not in python_filters
        }
        if python_filters:
            allowed, matched = _generic_match(instance, python_filters, authorizer)
            if not allowed or not matched:
                continue
        multiplicity = _filtered_query(
            lookup_source, _database_primary_key_for(instance), positive, native_filters
        ).count()
        if not multiplicity:
            continue
        excluded = False
        for negative_clause in negatives:

            def matches_negative(
                path: str,
                value: object,
                domains: Domains,
                *,
                instance: GeneralManager = instance,
            ) -> bool:
                nonlocal available
                prop = properties.get(path.split("__", 1)[0])
                if prop is not None and prop.query_annotation is None:
                    allowed, matched = _generic_match(
                        instance, {path: value}, authorizer
                    )
                    available &= allowed
                    return matched
                return _filtered_query(
                    lookup_source,
                    _database_primary_key_for(instance),
                    domains,
                    {path: value},
                ).exists()

            if negative_clause and all(
                matches_negative(path, value, domains)
                for path, value, domains in negative_clause
            ):
                excluded = True
                break
        if available and not excluded:
            kept.extend([instance] * multiplicity)
    return candidates.with_instances(kept)


def authorized_group(
    group: GroupManager[GeneralManager], info: ResolveInfo, field: str
) -> GroupManager[GeneralManager]:
    """Build a fresh field projection; never cache an actor-specific aggregate."""
    from general_manager.manager.group_manager import GroupManager

    members = filter_contributors(group.members, info, (field,))
    keys = dict(group._group_by_value)
    if field in keys and not members:
        # A reused group can have no members visible to the current actor.
        # Its saved grouping value is not an authorized field contribution.
        keys.pop(field)
    return GroupManager(group._manager_class, keys, members)


def native_scalar_path(manager: type[GeneralManager], path: str) -> bool:
    """Prove a native scalar/FK predicate without reading a protected value."""
    from general_manager.api.graphql_value_contract import _manager_source

    parts = path.split("__")
    attributes = manager.Interface.get_attribute_types()
    aliases, _names, _ = _operand_field_names(
        manager, parts[0], attributes, native_paths=False
    )
    if len(aliases) > 1:
        return False
    name = aliases[0] if aliases else parts[0]
    metadata = attributes.get(name)
    if metadata is None or metadata.get("relation_kind") == "collection":
        return False
    if not _manager_source(manager, name):
        return False
    target = resolve_general_manager_type(
        metadata.get("type"), get_graphql_manager_registry()
    )
    rest = parts[1:]
    if target is None:
        return not rest or (len(rest) == 1 and rest[0] in _LOOKUPS)
    if not rest:
        return True
    if (
        len(rest) == 1
        and rest[0] in _LOOKUPS
        and _relation_suffix_is_lookup(
            rest,
            target.Interface.get_attribute_types().keys()
            | target.Interface.get_graph_ql_properties().keys(),
        )
    ):
        return True
    return native_scalar_path(target, "__".join(rest))


def prefilter_native_scalars(
    candidates: Bucket[GeneralManager], plan: QueryParameterPlan
) -> Bucket[GeneralManager]:
    """Narrow safe scalar conjuncts early; never split a grouped exclusion."""
    if type(candidates) is not DatabaseBucket:
        return candidates
    manager = candidates._manager_class
    positive = {
        key: value
        for key, value in plan.filters.items()
        if native_scalar_path(manager, key)
    }
    if positive:
        candidates = candidates.filter(**positive)
    for group in (plan.excludes, plan.normalized_excludes):
        if group and all(native_scalar_path(manager, key) for key in group):
            candidates = candidates.exclude(**group)
    return candidates


def _plural_path(manager: type[GeneralManager], path: str) -> bool:
    parts = path.split("__")
    attributes = manager.Interface.get_attribute_types()
    aliases, _names, _ = _operand_field_names(
        manager, parts[0], attributes, native_paths=False
    )
    metadata = _relation_metadata(
        manager, aliases[0] if aliases else parts[0], attributes
    )
    if metadata.get("relation_kind") == "collection":
        return True
    target = resolve_general_manager_type(
        metadata.get("type"), get_graphql_manager_registry()
    )
    return bool(
        target is not None
        and len(parts) > 1
        and _plural_path(target, "__".join(parts[1:]))
    )


def _generic_match(
    instance: GeneralManager,
    predicates: Mapping[str, object],
    authorizer: ContributorAuthorization,
    parsed: ParsedFilters | None = None,
) -> tuple[bool, bool]:
    """Apply mapping predicates to the same readable witnesses on local sources."""
    from general_manager.utils.filter_parser import create_filter_function

    groups: dict[str, dict[str, object]] = {}
    for path, expected in predicates.items():
        root, _, rest = path.partition("__")
        groups.setdefault(root, {})[rest] = expected
    for root, criteria in groups.items():
        structure = authorizer.structures.for_instance(instance)
        attributes = (
            structure.attributes
            if structure is not None
            else instance.Interface.get_attribute_types()
        )
        aliases, names, _ = _operand_field_names(
            type(instance), root, attributes, native_paths=False
        )
        known = (
            set(attributes)
            | set(instance.Interface.get_graph_ql_properties())
            | set(instance.Interface.input_fields)
        )
        if not names.intersection(known):
            raise QueryFieldPermissionError(root)
        charged = [False]
        ordered_names = (
            (root, *sorted(names - {root})) if root in names else tuple(sorted(names))
        )
        if not all(
            authorizer.field_allowed(instance, name, charged=charged)
            for name in ordered_names
        ):
            return False, False
        name = aliases[0] if aliases else root
        definitions = parsed.get(name, {}) if parsed is not None else {}
        normalized = definitions.get("filter_kwargs", {})
        if normalized:
            criteria = {
                (
                    key if key in normalized else "id" if not key else key
                ): normalized.get(key, normalized.get("id") if not key else value)
                for key, value in criteria.items()
            }
        metadata = _relation_metadata(type(instance), name, attributes)
        target = resolve_general_manager_type(
            metadata.get("type"), get_graphql_manager_registry()
        )
        if (
            target is None
            and not definitions.get("filter_funcs")
            and "collection_criteria" not in definitions
            and authorizer.calculation_plan is not None
        ):
            from general_manager.bucket.calculation_bucket import CalculationBucket

            # This owner reads and normalizes the actual scalar/property. An
            # eager getattr here would evaluate that getter a second time.
            if not all(
                CalculationBucket._matches_manager_lookup(
                    instance,
                    root + ("__" + lookup if lookup else ""),
                    expected,
                    type(instance),
                )
                for lookup, expected in criteria.items()
            ):
                return True, False
            continue
        value = getattr(instance, name)
        if (
            "collection_criteria" in definitions
            and authorizer.calculation_plan is not None
        ):
            selection_members: list[object] | None = None if value is None else []
            for child in value if value is not None else ():
                assert selection_members is not None
                if not isinstance(child, GeneralManager):
                    selection_members.append(child)
                    continue
                if authorizer.row_allowed(child) and all(
                    authorizer.field_allowed(child, key) for key in child.identification
                ):
                    selection_members.append(child)
            input_field = authorizer.calculation_plan.input_fields[name]
            dependencies = {
                key: getattr(instance, key) for key in input_field.depends_on
            }
            if not authorizer.calculation_plan._collection_matches(
                name, selection_members, definitions, dependencies
            ):
                return True, False
            continue
        if target is None:
            if definitions.get("filter_funcs"):
                matched = all(
                    function(value) for function in definitions["filter_funcs"]
                )
            else:
                matched = all(
                    create_filter_function(lookup, expected)(value)
                    for lookup, expected in criteria.items()
                )
            if not matched:
                return True, False
            continue
        plural = isinstance(value, (Bucket, list, tuple, set))
        relation_isnull = (
            not plural
            # Deferred calculation predicates retain their owning frozen
            # lookup grammar, which has no relation-level isnull operator.
            and authorizer.calculation_plan is None
            and "isnull" in criteria
            and _relation_suffix_is_lookup(
                ("isnull",),
                target.Interface.get_attribute_types().keys()
                | target.Interface.get_graph_ql_properties().keys(),
            )
        )
        null_matches = (
            (value is None) is bool(criteria["isnull"]) if relation_isnull else True
        )
        child_criteria = (
            {key: expected for key, expected in criteria.items() if key != "isnull"}
            if relation_isnull
            else criteria
        )
        if value is None:
            if not null_matches or not all(
                create_filter_function(lookup, expected)(None)
                for lookup, expected in child_criteria.items()
            ):
                return True, False
            continue
        members = value if plural else (value,)
        matched = False
        for child in members:
            if not isinstance(child, GeneralManager):
                message = "Cannot authorize ambiguous query relation."
                raise GraphQLError(message)
            if not authorizer.row_allowed(child):
                if not plural:
                    return False, False
                continue
            if relation_isnull:
                from general_manager.api.graphql_resolvers import (
                    relation_id_alias_target_fields,
                )

                identity_fields = (
                    relation_id_alias_target_fields(instance.Interface, name)
                    if name in attributes and metadata.get("relation_kind") == "direct"
                    else tuple(child.identification)
                )
                if not all(
                    authorizer.field_allowed(child, key) for key in identity_fields
                ):
                    return False, False
                if not null_matches:
                    continue
            allowed, matches = _generic_match(
                child,
                {key or "id": expected for key, expected in child_criteria.items()},
                authorizer,
            )
            if allowed and matches:
                matched = True
                # Evaluate every visible occurrence, retaining provider errors.
            elif not plural and not allowed:
                return False, False
        if isinstance(value, RequestBucket) and not value.response_is_complete:
            raise GraphQLError(INCOMPLETE_ERROR)
        if not matched:
            return True, False
    return True, True
