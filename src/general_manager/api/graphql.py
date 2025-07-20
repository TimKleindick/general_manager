from __future__ import annotations
import graphene
from typing import Any, Callable, get_args, TYPE_CHECKING, cast, Type
from decimal import Decimal
from datetime import date, datetime
import json

from general_manager.measurement.measurement import Measurement
from general_manager.manager.generalManager import GeneralManagerMeta, GeneralManager
from general_manager.api.property import GraphQLProperty
from general_manager.bucket.baseBucket import Bucket
from general_manager.interface.baseInterface import InterfaceBase
from django.db.models import NOT_PROVIDED
from django.core.exceptions import ValidationError

from graphql import GraphQLError


if TYPE_CHECKING:
    from general_manager.permission.basePermission import BasePermission
    from graphene import ResolveInfo as GraphQLResolveInfo


class MeasurementType(graphene.ObjectType):
    value = graphene.Float()
    unit = graphene.String()


class PageInfo(graphene.ObjectType):
    total_count = graphene.Int(required=True)
    page_size = graphene.Int(required=False)
    current_page = graphene.Int(required=True)
    total_pages = graphene.Int(required=True)


def getReadPermissionFilter(
    generalManagerClass: GeneralManagerMeta,
    info: GraphQLResolveInfo,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """
    Returns a list of filter and exclude dictionaries based on the read permissions for the specified manager class and user context.
    
    Each tuple in the returned list contains a filter dictionary and an exclude dictionary, representing permission-based constraints to be applied to queries.
    """
    filters = []
    PermissionClass: type[BasePermission] | None = getattr(
        generalManagerClass, "Permission", None
    )
    if PermissionClass:
        permission_filters = PermissionClass(
            generalManagerClass, info.context.user
        ).getPermissionFilter()
        for permission_filter in permission_filters:
            filter_dict = permission_filter.get("filter", {})
            exclude_dict = permission_filter.get("exclude", {})
            filters.append((filter_dict, exclude_dict))
    return filters


class GraphQL:
    """
    Baut die GraphQL-Oberfläche auf und erstellt Resolver-Funktionen
    dynamisch für die angegebene GeneralManager-Klasse.
    """

    _query_class: type[graphene.ObjectType] | None = None
    _mutation_class: type[graphene.ObjectType] | None = None
    _mutations: dict[str, Any] = {}
    _query_fields: dict[str, Any] = {}
    _page_type_registry: dict[str, type[graphene.ObjectType]] = {}
    graphql_type_registry: dict[str, type] = {}
    graphql_filter_type_registry: dict[str, type] = {}

    @classmethod
    def createGraphqlMutation(cls, generalManagerClass: type[GeneralManager]) -> None:
        """
        Creates and registers GraphQL mutation classes (create, update, delete) for the given manager class if its interface overrides the corresponding base methods.
        
        For each supported mutation, generates a GraphQL mutation class with appropriate input and output fields, and adds it to the mutation registry.
        """

        interface_cls: InterfaceBase | None = getattr(
            generalManagerClass, "Interface", None
        )
        if not interface_cls:
            return

        default_return_values = {
            "success": graphene.Boolean(),
            generalManagerClass.__name__: graphene.Field(
                lambda: GraphQL.graphql_type_registry[generalManagerClass.__name__]
            ),
        }
        if InterfaceBase.create.__code__ != interface_cls.create.__code__:
            create_name = f"create{generalManagerClass.__name__}"
            cls._mutations[create_name] = cls.generateCreateMutationClass(
                generalManagerClass, default_return_values
            )

        if InterfaceBase.update.__code__ != interface_cls.update.__code__:
            update_name = f"update{generalManagerClass.__name__}"
            cls._mutations[update_name] = cls.generateUpdateMutationClass(
                generalManagerClass, default_return_values
            )

        if InterfaceBase.deactivate.__code__ != interface_cls.deactivate.__code__:
            delete_name = f"delete{generalManagerClass.__name__}"
            cls._mutations[delete_name] = cls.generateDeleteMutationClass(
                generalManagerClass, default_return_values
            )

    @classmethod
    def createGraphqlInterface(cls, generalManagerClass: GeneralManagerMeta) -> None:
        """
        Creates and registers a GraphQL ObjectType for the given GeneralManager subclass.
        
        This method introspects the manager's interface and GraphQLProperty fields, maps them to Graphene fields with appropriate resolvers, registers the resulting type in the internal registry, and adds corresponding query fields to the schema.
        """
        interface_cls: InterfaceBase | None = getattr(
            generalManagerClass, "Interface", None
        )
        if not interface_cls:
            return

        graphene_type_name = f"{generalManagerClass.__name__}Type"
        fields: dict[str, Any] = {}

        # Map Attribute Types to Graphene Fields
        for field_name, field_info in interface_cls.getAttributeTypes().items():
            field_type = field_info["type"]
            fields[field_name] = cls._mapFieldToGrapheneRead(field_type, field_name)
            resolver_name = f"resolve_{field_name}"
            fields[resolver_name] = cls._createResolver(field_name, field_type)

        # handle GraphQLProperty attributes
        for attr_name, attr_value in generalManagerClass.__dict__.items():
            if isinstance(attr_value, GraphQLProperty):
                type_hints = get_args(attr_value.graphql_type_hint)
                field_type = (
                    type_hints[0]
                    if type_hints
                    else cast(type, attr_value.graphql_type_hint)
                )
                fields[attr_name] = cls._mapFieldToGrapheneRead(field_type, attr_name)
                fields[f"resolve_{attr_name}"] = cls._createResolver(
                    attr_name, field_type
                )

        graphene_type = type(graphene_type_name, (graphene.ObjectType,), fields)
        cls.graphql_type_registry[generalManagerClass.__name__] = graphene_type
        cls._addQueriesToSchema(graphene_type, generalManagerClass)

    @staticmethod
    def _sortByOptions(
        generalManagerClass: GeneralManagerMeta,
    ) -> type[graphene.Enum] | None:
        """
        Generate a Graphene Enum type listing the sortable fields for a given GeneralManager class.
        
        Returns:
            A Graphene Enum type with options for each sortable attribute, including separate options for the value and unit of Measurement fields, or None if no sortable fields are found.
        """
        sort_options = []
        for (
            field_name,
            field_info,
        ) in generalManagerClass.Interface.getAttributeTypes().items():
            field_type = field_info["type"]
            if issubclass(field_type, GeneralManager):
                continue
            elif issubclass(field_type, Measurement):
                sort_options.append(f"{field_name}_value")
                sort_options.append(f"{field_name}_unit")
            else:
                sort_options.append(field_name)

        if not sort_options:
            return None

        return type(
            f"{generalManagerClass.__name__}SortByOptions",
            (graphene.Enum,),
            {option: option for option in sort_options},
        )

    @staticmethod
    def _createFilterOptions(
        field_name: str, field_type: GeneralManagerMeta
    ) -> type[graphene.InputObjectType] | None:
        """
        Dynamically generates a Graphene InputObjectType for filtering fields of a GeneralManager subclass.
        
        Creates filter fields for each attribute based on its type, supporting numeric and string filter operations, and specialized handling for Measurement attributes. Returns the generated InputObjectType, or None if no applicable filter fields exist.
        """
        number_options = ["exact", "gt", "gte", "lt", "lte"]
        string_options = [
            "exact",
            "icontains",
            "contains",
            "in",
            "startswith",
            "endswith",
        ]

        graphene_filter_type_name = f"{field_type.__name__}FilterType"
        if graphene_filter_type_name in GraphQL.graphql_filter_type_registry:
            return GraphQL.graphql_filter_type_registry[graphene_filter_type_name]

        filter_fields = {}
        for attr_name, attr_info in field_type.Interface.getAttributeTypes().items():
            attr_type = attr_info["type"]
            if issubclass(attr_type, GeneralManager):
                continue
            elif issubclass(attr_type, Measurement):
                filter_fields[f"{attr_name}_value"] = graphene.Float()
                filter_fields[f"{attr_name}_unit"] = graphene.String()
                for option in number_options:
                    filter_fields[f"{attr_name}_value__{option}"] = graphene.Float()
                    filter_fields[f"{attr_name}_unit__{option}"] = graphene.String()
            else:
                filter_fields[attr_name] = GraphQL._mapFieldToGrapheneRead(
                    attr_type, attr_name
                )
                if issubclass(attr_type, (int, float, Decimal, date, datetime)):
                    for option in number_options:
                        filter_fields[f"{attr_name}__{option}"] = (
                            GraphQL._mapFieldToGrapheneRead(attr_type, attr_name)
                        )
                elif issubclass(attr_type, str):
                    for option in string_options:
                        filter_fields[f"{attr_name}__{option}"] = (
                            GraphQL._mapFieldToGrapheneRead(attr_type, attr_name)
                        )
        if not filter_fields:
            return None

        filter_class = type(
            graphene_filter_type_name,
            (graphene.InputObjectType,),
            filter_fields,
        )
        GraphQL.graphql_filter_type_registry[graphene_filter_type_name] = filter_class
        return filter_class

    @staticmethod
    def _mapFieldToGrapheneRead(field_type: type, field_name: str) -> Any:
        """
        Maps a Python field type and name to the appropriate Graphene field for GraphQL schema generation.
        
        For `Measurement` fields, returns a field with an optional `target_unit` argument. For `GeneralManager` subclasses, returns a paginated field with filtering, exclusion, sorting, pagination, and grouping arguments if the field name ends with `_list`; otherwise, returns a single object field. For all other types, returns the corresponding Graphene scalar field.
        """
        if issubclass(field_type, Measurement):
            return graphene.Field(MeasurementType, target_unit=graphene.String())
        elif issubclass(field_type, GeneralManager):
            if field_name.endswith("_list"):
                attributes = {
                    "reverse": graphene.Boolean(),
                    "page": graphene.Int(),
                    "page_size": graphene.Int(),
                    "group_by": graphene.List(graphene.String),
                }
                filter_options = GraphQL._createFilterOptions(field_name, field_type)
                if filter_options:
                    attributes["filter"] = filter_options()
                    attributes["exclude"] = filter_options()

                sort_by_options = GraphQL._sortByOptions(field_type)
                if sort_by_options:
                    attributes["sort_by"] = sort_by_options()

                page_type = GraphQL._getOrCreatePageType(
                    field_type.__name__ + "Page",
                    lambda: GraphQL.graphql_type_registry[field_type.__name__],
                )
                return graphene.Field(page_type, **attributes)

                return graphene.List(
                    lambda: GraphQL.graphql_type_registry[field_type.__name__],
                    **attributes,
                )
            return graphene.Field(
                lambda: GraphQL.graphql_type_registry[field_type.__name__]
            )
        else:
            return GraphQL._mapFieldToGrapheneBaseType(field_type)()

    @staticmethod
    def _mapFieldToGrapheneBaseType(field_type: type) -> Type[Any]:
        """
        Ordnet einen Python-Typ einem entsprechenden Graphene-Feld zu.
        """
        if issubclass(field_type, str):
            return graphene.String
        elif issubclass(field_type, bool):
            return graphene.Boolean
        elif issubclass(field_type, int):
            return graphene.Int
        elif issubclass(field_type, (float, Decimal)):
            return graphene.Float
        elif issubclass(field_type, (date, datetime)):
            return graphene.Date
        else:
            return graphene.String

    @staticmethod
    def _parseInput(input_val: dict[str, Any] | str | None) -> dict[str, Any]:
        """
        Wandelt einen als JSON-String oder Dict gelieferten Filter/Exclude-Parameter in ein Dict um.
        """
        if input_val is None:
            return {}
        if isinstance(input_val, str):
            try:
                return json.loads(input_val)
            except Exception:
                return {}
        return input_val

    @staticmethod
    def _applyQueryParameters(
        queryset: Bucket[GeneralManager],
        filter_input: dict[str, Any] | str | None,
        exclude_input: dict[str, Any] | str | None,
        sort_by: graphene.Enum | None,
        reverse: bool,
    ) -> Bucket[GeneralManager]:
        """
        Applies filtering, exclusion, and sorting parameters to a queryset.
        
        Parameters:
            queryset (Bucket[GeneralManager]): The queryset to modify.
            filter_input (dict | str | None): Filters to apply, as a dict or JSON string.
            exclude_input (dict | str | None): Exclusions to apply, as a dict or JSON string.
            sort_by (graphene.Enum | None): Field to sort by, if provided.
            reverse (bool): Whether to reverse the sort order.
        
        Returns:
            Bucket[GeneralManager]: The modified queryset after applying filters, exclusions, and sorting.
        """
        filters = GraphQL._parseInput(filter_input)
        if filters:
            queryset = queryset.filter(**filters)

        excludes = GraphQL._parseInput(exclude_input)
        if excludes:
            queryset = queryset.exclude(**excludes)

        if sort_by:
            sort_by_str = cast(str, getattr(sort_by, "value", sort_by))
            queryset = queryset.sort(sort_by_str, reverse=reverse)

        return queryset

    @staticmethod
    def _applyPermissionFilters(
        queryset: Bucket,
        general_manager_class: type[GeneralManager],
        info: GraphQLResolveInfo,
    ) -> Bucket:
        """
        Wendet die vom Permission-Interface vorgegebenen Filter auf das Queryset an.
        """
        permission_filters = getReadPermissionFilter(general_manager_class, info)
        filtered_queryset = queryset
        for perm_filter, perm_exclude in permission_filters:
            qs_perm = queryset.exclude(**perm_exclude).filter(**perm_filter)
            filtered_queryset = filtered_queryset | qs_perm

        return filtered_queryset

    @staticmethod
    def _checkReadPermission(
        instance: GeneralManager, info: GraphQLResolveInfo, field_name: str
    ) -> bool:
        """
        Überprüft, ob der Benutzer Lesezugriff auf das jeweilige Feld hat.
        """
        PermissionClass: type[BasePermission] | None = getattr(
            instance, "Permission", None
        )
        if PermissionClass:
            return PermissionClass(instance, info.context.user).checkPermission(
                "read", field_name
            )
        return True

    @staticmethod
    def _createListResolver(
        base_getter: Callable[[Any], Any], fallback_manager_class: type[GeneralManager]
    ) -> Callable[..., Any]:
        """
        Creates a resolver for GraphQL list fields that returns paginated, filtered, sorted, and optionally grouped results with permission checks applied.
        
        The returned resolver processes list queries by applying permission-based filtering, user-specified filters and exclusions, sorting, grouping, and pagination. It returns a dictionary containing the paginated items and pagination metadata.
        """

        def resolver(
            self: GeneralManager,
            info: GraphQLResolveInfo,
            filter: dict[str, Any] | str | None = None,
            exclude: dict[str, Any] | str | None = None,
            sort_by: graphene.Enum | None = None,
            reverse: bool = False,
            page: int | None = None,
            page_size: int | None = None,
            group_by: list[str] | None = None,
        ) -> dict[str, Any]:
            """
            Resolves a list field by returning paginated, filtered, sorted, and optionally grouped results with permission checks applied.
            
            Parameters:
                filter (dict[str, Any] | str | None): Filter criteria as a dictionary or JSON string.
                exclude (dict[str, Any] | str | None): Exclusion criteria as a dictionary or JSON string.
                sort_by (graphene.Enum | None): Field to sort by.
                reverse (bool): Whether to reverse the sort order.
                page (int | None): Page number for pagination.
                page_size (int | None): Number of items per page.
                group_by (list[str] | None): List of field names to group results by.
            
            Returns:
                dict[str, Any]: A dictionary containing the paginated items under "items" and pagination metadata under "pageInfo".
            """
            base_queryset = base_getter(self)
            # use _manager_class from the attribute if available, otherwise fallback
            manager_class = getattr(
                base_queryset, "_manager_class", fallback_manager_class
            )
            qs = GraphQL._applyPermissionFilters(base_queryset, manager_class, info)
            qs = GraphQL._applyQueryParameters(qs, filter, exclude, sort_by, reverse)
            qs = GraphQL._applyGrouping(qs, group_by)

            total_count = len(qs)

            qs_paginated = GraphQL._applyPagination(qs, page, page_size)

            page_info = {
                "total_count": total_count,
                "page_size": page_size,
                "current_page": page or 1,
                "total_pages": (
                    ((total_count + page_size - 1) // page_size) if page_size else 1
                ),
            }
            return {
                "items": qs_paginated,
                "pageInfo": page_info,
            }

        return resolver

    @staticmethod
    def _applyPagination(
        queryset: Bucket[GeneralManager], page: int | None, page_size: int | None
    ) -> Bucket[GeneralManager]:
        """
        Return a paginated subset of the queryset based on the specified page number and page size.
        
        If either `page` or `page_size` is provided, pagination is applied; otherwise, the original queryset is returned.
        """
        if page is not None or page_size is not None:
            page = page or 1
            page_size = page_size or 10
            offset = (page - 1) * page_size
            queryset = cast(Bucket, queryset[offset : offset + page_size])
        return queryset

    @staticmethod
    def _applyGrouping(
        queryset: Bucket[GeneralManager], group_by: list[str] | None
    ) -> Bucket[GeneralManager]:
        """
        Group a queryset by the specified fields.
        
        If `group_by` is `[""]`, groups by all default fields. If `group_by` is a list of field names, groups by those fields. Returns the grouped queryset.
        """
        if group_by is not None:
            if group_by == [""]:
                queryset = queryset.group_by()
            else:
                queryset = queryset.group_by(*group_by)
        return queryset

    @staticmethod
    def _createMeasurementResolver(field_name: str) -> Callable[..., Any]:
        """
        Creates a resolver function for Measurement fields that returns the value and unit, optionally converting to a specified target unit.
        
        Parameters:
            field_name (str): The name of the Measurement field to resolve.
        
        Returns:
            Callable[..., dict[str, Any] | None]: A resolver that returns a dictionary with 'value' and 'unit' keys, or None if permission is denied or the field is not a Measurement.
        """

        def resolver(
            self: GeneralManager,
            info: GraphQLResolveInfo,
            target_unit: str | None = None,
        ) -> dict[str, Any] | None:
            if not GraphQL._checkReadPermission(self, info, field_name):
                return None
            result = getattr(self, field_name)
            if not isinstance(result, Measurement):
                return None
            if target_unit:
                result = result.to(target_unit)
            return {
                "value": result.quantity.magnitude,
                "unit": result.quantity.units,
            }

        return resolver

    @staticmethod
    def _createNormalResolver(field_name: str) -> Callable[..., Any]:
        """
        Erzeugt einen Resolver für Standardfelder (keine Listen, keine Measurements).
        """

        def resolver(self: GeneralManager, info: GraphQLResolveInfo) -> Any:
            if not GraphQL._checkReadPermission(self, info, field_name):
                return None
            return getattr(self, field_name)

        return resolver

    @classmethod
    def _createResolver(cls, field_name: str, field_type: type) -> Callable[..., Any]:
        """
        Selects and returns the appropriate resolver function for a given field based on its type and name.
        
        For fields ending with `_list` and referencing a `GeneralManager` subclass, returns a list resolver supporting pagination and filtering. For `Measurement` fields, returns a measurement resolver. For all other fields, returns a standard resolver.
        """
        if field_name.endswith("_list") and issubclass(field_type, GeneralManager):
            return cls._createListResolver(
                lambda self: getattr(self, field_name), field_type
            )
        if issubclass(field_type, Measurement):
            return cls._createMeasurementResolver(field_name)
        return cls._createNormalResolver(field_name)

    @classmethod
    def _getOrCreatePageType(
        cls,
        page_type_name: str,
        item_type: type[graphene.ObjectType] | Callable[[], type[graphene.ObjectType]],
    ) -> type[graphene.ObjectType]:
        """
        Return a paginated GraphQL ObjectType for the given item type, creating and caching it if it does not already exist.
        
        Parameters:
            page_type_name (str): The name to use for the paginated type.
        
        Returns:
            type[graphene.ObjectType]: A GraphQL ObjectType with `items` (list of item_type) and `pageInfo` (pagination metadata).
        """
        if page_type_name not in cls._page_type_registry:
            cls._page_type_registry[page_type_name] = type(
                page_type_name,
                (graphene.ObjectType,),
                {
                    "items": graphene.List(item_type, required=True),
                    "pageInfo": graphene.Field(PageInfo, required=True),
                },
            )
        return cls._page_type_registry[page_type_name]

    @classmethod
    def _addQueriesToSchema(
        cls, graphene_type: type, generalManagerClass: GeneralManagerMeta
    ) -> None:
        """
        Adds paginated list and single-item query fields for a GeneralManager subclass to the GraphQL schema.
        
        The list query field supports filtering, exclusion, sorting, pagination, and grouping, returning a paginated result with metadata. The single-item query field retrieves an instance by its identification fields. Both queries are registered with their corresponding resolvers.
        """
        if not issubclass(generalManagerClass, GeneralManager):
            raise TypeError(
                "generalManagerClass must be a subclass of GeneralManager to create a GraphQL interface"
            )

        if not hasattr(cls, "_query_fields"):
            cls._query_fields: dict[str, Any] = {}

        # resolver and field for the list query
        list_field_name = f"{generalManagerClass.__name__.lower()}_list"
        attributes = {
            "reverse": graphene.Boolean(),
            "page": graphene.Int(),
            "page_size": graphene.Int(),
            "group_by": graphene.List(graphene.String),
        }
        filter_options = cls._createFilterOptions(
            generalManagerClass.__name__.lower(), generalManagerClass
        )
        if filter_options:
            attributes["filter"] = filter_options()
            attributes["exclude"] = filter_options()
        sort_by_options = cls._sortByOptions(generalManagerClass)
        if sort_by_options:
            attributes["sort_by"] = sort_by_options()

        page_type = cls._getOrCreatePageType(
            graphene_type.__name__ + "Page", graphene_type
        )
        list_field = graphene.Field(page_type, **attributes)

        list_resolver = cls._createListResolver(
            lambda self: generalManagerClass.all(), generalManagerClass
        )
        cls._query_fields[list_field_name] = list_field
        cls._query_fields[f"resolve_{list_field_name}"] = list_resolver

        # resolver and field for the single item query
        item_field_name = generalManagerClass.__name__.lower()
        identification_fields = {}
        for (
            input_field_name,
            input_field,
        ) in generalManagerClass.Interface.input_fields.items():
            if issubclass(input_field.type, GeneralManager):
                key = f"{input_field_name}_id"
                identification_fields[key] = graphene.Int(required=True)
            elif input_field_name == "id":
                identification_fields[input_field_name] = graphene.ID(required=True)
            else:
                identification_fields[input_field_name] = cls._mapFieldToGrapheneRead(
                    input_field.type, input_field_name
                )
                identification_fields[input_field_name].required = True

        item_field = graphene.Field(graphene_type, **identification_fields)

        def resolver(
            self: GeneralManager, info: GraphQLResolveInfo, **identification: dict
        ) -> GeneralManager:
            return generalManagerClass(**identification)

        cls._query_fields[item_field_name] = item_field
        cls._query_fields[f"resolve_{item_field_name}"] = resolver

    @classmethod
    def createWriteFields(cls, interface_cls: InterfaceBase) -> dict[str, Any]:
        """
        Generate a dictionary of Graphene input fields for mutations based on the attributes of the provided interface class.
        
        Skips system-managed and derived attributes. For attributes referencing `GeneralManager` subclasses, uses an ID or list of IDs as appropriate. Other types are mapped to their corresponding Graphene scalar types. Each field is annotated with an `editable` attribute. An optional `history_comment` field, also marked as editable, is always included.
        
        Returns:
            dict[str, Any]: Mapping of attribute names to Graphene input fields for mutation arguments.
        """
        fields: dict[str, Any] = {}

        for name, info in interface_cls.getAttributeTypes().items():
            if name in ["changed_by", "created_at", "updated_at"]:
                continue
            if info["is_derived"]:
                continue

            typ = info["type"]
            req = info["is_required"]
            default = info["default"]

            if issubclass(typ, GeneralManager):
                if name.endswith("_list"):
                    fld = graphene.List(
                        graphene.ID,
                        required=req,
                        default_value=default,
                    )
                else:
                    fld = graphene.ID(
                        required=req,
                        default_value=default,
                    )
            else:
                base_cls = cls._mapFieldToGrapheneBaseType(typ)
                fld = base_cls(
                    required=req,
                    default_value=default,
                )

            # mark for generate* code to know what is editable
            setattr(fld, "editable", info["is_editable"])
            fields[name] = fld

        # history_comment is always optional without a default value
        fields["history_comment"] = graphene.String()
        setattr(fields["history_comment"], "editable", True)

        return fields

    @classmethod
    def generateCreateMutationClass(
        cls,
        generalManagerClass: type[GeneralManager],
        default_return_values: dict[str, Any],
    ) -> type[graphene.Mutation] | None:
        """
        Dynamically generates a Graphene mutation class for creating an instance of a specified GeneralManager subclass.
        
        The generated mutation class accepts input fields defined by the manager's interface, filters out fields with `NOT_PROVIDED` values, and calls the manager's `create` method with the provided arguments and the current user's ID. If creation succeeds, it returns a dictionary with a success flag and the created instance; if an error occurs, a GraphQL error is raised. Returns None if the manager class does not define an interface.
        
        Returns:
            The generated Graphene mutation class, or None if the manager class does not define an interface.
        """
        interface_cls: InterfaceBase | None = getattr(
            generalManagerClass, "Interface", None
        )
        if not interface_cls:
            return

        def create_mutation(
            self,
            info: GraphQLResolveInfo,
            **kwargs: Any,
        ) -> dict:
            """
            Creates a new instance of the manager class with the provided arguments.
            
            Filters out fields set to `NOT_PROVIDED` before creation. Returns a dictionary containing a success flag and the created instance keyed by the manager class name. Raises a GraphQL error if creation fails.
            """
            try:
                kwargs = {
                    field_name: value
                    for field_name, value in kwargs.items()
                    if value is not NOT_PROVIDED
                }
                instance = generalManagerClass.create(
                    **kwargs, creator_id=info.context.user.id
                )
            except Exception as e:
                GraphQL._handleGraphQLError(e)
                return {
                    "success": False,
                }

            return {
                "success": True,
                generalManagerClass.__name__: instance,
            }

        return type(
            f"Create{generalManagerClass.__name__}",
            (graphene.Mutation,),
            {
                **default_return_values,
                "__doc__": f"Mutation to create {generalManagerClass.__name__}",
                "Arguments": type(
                    "Arguments",
                    (),
                    {
                        field_name: field
                        for field_name, field in cls.createWriteFields(
                            interface_cls
                        ).items()
                        if field_name not in generalManagerClass.Interface.input_fields
                    },
                ),
                "mutate": create_mutation,
            },
        )

    @classmethod
    def generateUpdateMutationClass(
        cls,
        generalManagerClass: type[GeneralManager],
        default_return_values: dict[str, Any],
    ) -> type[graphene.Mutation] | None:
        """
        Generates a GraphQL mutation class for updating an instance of a GeneralManager subclass.
        
        The generated mutation accepts editable fields as arguments, invokes the manager's `update` method with the provided values and the current user's ID, and returns a dictionary containing the operation's success status and the updated instance. If the manager class does not define an `Interface`, returns None.
        
        Returns:
            The generated Graphene mutation class, or None if no interface is defined.
        """
        interface_cls: InterfaceBase | None = getattr(
            generalManagerClass, "Interface", None
        )
        if not interface_cls:
            return

        def update_mutation(
            self,
            info: GraphQLResolveInfo,
            **kwargs: Any,
        ) -> dict:
            """
            Updates an instance of the specified GeneralManager class with the provided field values.
            
            Parameters:
                info (GraphQLResolveInfo): The GraphQL resolver context, containing user and request information.
                **kwargs: Fields to update, including the required 'id' of the instance.
            
            Returns:
                dict: Contains 'success' (bool) and the updated instance keyed by its class name.
            """
            try:
                manager_id = kwargs.pop("id", None)
                instance = generalManagerClass(manager_id).update(
                    creator_id=info.context.user.id, **kwargs
                )
            except Exception as e:
                GraphQL._handleGraphQLError(e)
                return {
                    "success": False,
                }

            return {
                "success": True,
                generalManagerClass.__name__: instance,
            }

        return type(
            f"Create{generalManagerClass.__name__}",
            (graphene.Mutation,),
            {
                **default_return_values,
                "__doc__": f"Mutation to update {generalManagerClass.__name__}",
                "Arguments": type(
                    "Arguments",
                    (),
                    {
                        field_name: field
                        for field_name, field in cls.createWriteFields(
                            interface_cls
                        ).items()
                        if field.editable
                    },
                ),
                "mutate": update_mutation,
            },
        )

    @classmethod
    def generateDeleteMutationClass(
        cls,
        generalManagerClass: type[GeneralManager],
        default_return_values: dict[str, Any],
    ) -> type[graphene.Mutation] | None:
        """
        Generates a GraphQL mutation class for deactivating (soft-deleting) an instance of a GeneralManager subclass.
        
        The generated mutation accepts input fields defined by the manager's interface, deactivates the specified instance using its ID, and returns a dictionary containing a success status and the deactivated instance keyed by the class name. If the manager class does not define an interface, returns None.
        
        Returns:
            The generated Graphene mutation class, or None if no interface is defined.
        """
        interface_cls: InterfaceBase | None = getattr(
            generalManagerClass, "Interface", None
        )
        if not interface_cls:
            return

        def delete_mutation(
            self,
            info: GraphQLResolveInfo,
            **kwargs: Any,
        ) -> dict:
            """
            Deactivates an instance of the specified GeneralManager class and returns the result.
            
            Parameters:
                info (GraphQLResolveInfo): GraphQL resolver context containing user information.
                **kwargs: Arguments including the instance ID to deactivate.
            
            Returns:
                dict: Contains "success" (bool) and the deactivated instance keyed by its class name.
            """
            try:
                manager_id = kwargs.pop("id", None)
                instance = generalManagerClass(manager_id).deactivate(
                    creator_id=info.context.user.id
                )
            except Exception as e:
                GraphQL._handleGraphQLError(e)
                return {
                    "success": False,
                }

            return {
                "success": True,
                generalManagerClass.__name__: instance,
            }

        return type(
            f"Delete{generalManagerClass.__name__}",
            (graphene.Mutation,),
            {
                **default_return_values,
                "__doc__": f"Mutation to delete {generalManagerClass.__name__}",
                "Arguments": type(
                    "Arguments",
                    (),
                    {
                        field_name: field
                        for field_name, field in cls.createWriteFields(
                            interface_cls
                        ).items()
                        if field_name in generalManagerClass.Interface.input_fields
                    },
                ),
                "mutate": delete_mutation,
            },
        )

    @staticmethod
    def _handleGraphQLError(error: Exception) -> None:
        """
        Raises a GraphQLError with an appropriate error code based on the type of exception.
        
        If the error is a PermissionError, the code is set to "PERMISSION_DENIED". For ValueError or ValidationError, the code is "BAD_USER_INPUT". All other exceptions result in a code of "INTERNAL_SERVER_ERROR".
        """
        if isinstance(error, PermissionError):
            raise GraphQLError(
                str(error),
                extensions={
                    "code": "PERMISSION_DENIED",
                },
            )
        elif isinstance(error, (ValueError, ValidationError)):
            raise GraphQLError(
                str(error),
                extensions={
                    "code": "BAD_USER_INPUT",
                },
            )
        else:
            raise GraphQLError(
                str(error),
                extensions={
                    "code": "INTERNAL_SERVER_ERROR",
                },
            )
