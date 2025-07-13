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

if TYPE_CHECKING:
    from general_manager.permission.basePermission import BasePermission
    from graphene import ResolveInfo as GraphQLResolveInfo


class MeasurementType(graphene.ObjectType):
    value = graphene.Float()
    unit = graphene.String()


def getReadPermissionFilter(
    generalManagerClass: GeneralManagerMeta,
    info: GraphQLResolveInfo,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """
    Ermittelt die Filter, die auf Basis der read-Permission für den angegebenen
    Manager angewendet werden müssen.
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
    graphql_type_registry: dict[str, type] = {}
    graphql_filter_type_registry: dict[str, type] = {}

    @classmethod
    def createGraphqlMutation(cls, generalManagerClass: type[GeneralManager]) -> None:
        """
        Erzeugt ein GraphQL-Mutation-Interface für die übergebene Manager-Klasse.
        Dabei werden:
          - Attribute aus dem Interface in Graphene-Felder abgebildet
          - Zu jedem Feld ein Resolver generiert und hinzugefügt
          - Der neue Type in das Registry eingetragen und Mutationen angehängt.
        """

        interface_cls: InterfaceBase | None = getattr(
            generalManagerClass, "Interface", None
        )
        if not interface_cls:
            return

        default_return_values = {
            "success": graphene.Boolean(),
            "errors": graphene.List(graphene.String),
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
        Generates and registers a GraphQL ObjectType for the specified GeneralManager class.
        
        This method maps interface attributes and GraphQLProperty fields to Graphene fields, creates appropriate resolvers, registers the resulting type in the internal registry, and adds related query fields to the schema.
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
        Creates a Graphene Enum type representing sortable fields for a given GeneralManager class.
        
        Returns:
            A Graphene Enum type with options for each sortable attribute, or None if no sortable fields exist.
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
        
        Creates filter fields for each attribute based on its type, supporting numeric and string filter options, as well as specialized handling for Measurement attributes. Returns the generated InputObjectType or None if no filter fields are applicable.
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
        
        For `Measurement` types, returns a field with an optional `target_unit` argument. For `GeneralManager` subclasses, returns either a list field (with optional filtering, exclusion, sorting, pagination, and grouping arguments if available) or a single field, depending on the field name. For other types, returns the corresponding Graphene scalar field.
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
        page: int | None,
        page_size: int | None,
    ) -> Bucket[GeneralManager]:
        """
        Wendet Filter, Excludes, Sortierung und Paginierung auf das Queryset an.
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

        if page is not None or page_size is not None:
            page = page or 1
            page_size = page_size or 10
            offset = (page - 1) * page_size
            queryset = cast(Bucket, queryset[offset : offset + page_size])

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
        Create a resolver function for GraphQL list fields that retrieves and returns a queryset with permission filters, query filters, sorting, pagination, and optional grouping applied.
        
        Parameters:
            base_getter: Function to obtain the base queryset from the parent instance.
            fallback_manager_class: Manager class to use if the queryset does not specify one.
        
        Returns:
            A resolver function that processes list queries with filtering, sorting, pagination, and grouping.
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
        ) -> Any:
            """
            Resolves a list field by returning a queryset with permission filters, query filters, sorting, pagination, and optional grouping applied.
            
            Parameters:
                filter: Filter criteria as a dictionary or JSON string.
                exclude: Exclusion criteria as a dictionary or JSON string.
                sort_by: Field to sort by, as a Graphene Enum.
                reverse: Whether to reverse the sort order.
                page: Page number for pagination.
                page_size: Number of items per page.
                group_by: List of field names to group results by.
            
            Returns:
                The resulting queryset after applying permissions, filters, sorting, pagination, and grouping.
            """
            base_queryset = base_getter(self)
            # use _manager_class from the attribute if available, otherwise fallback
            manager_class = getattr(
                base_queryset, "_manager_class", fallback_manager_class
            )
            qs = GraphQL._applyPermissionFilters(base_queryset, manager_class, info)
            qs = GraphQL._applyQueryParameters(
                qs, filter, exclude, sort_by, reverse, page, page_size
            )
            if group_by is not None:
                if group_by == [""]:
                    qs = qs.group_by()
                else:
                    qs = qs.group_by(*group_by)
            return qs

        return resolver

    @staticmethod
    def _createMeasurementResolver(field_name: str) -> Callable[..., Any]:
        """
        Erzeugt einen Resolver für Felder vom Typ Measurement.
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
        Wählt anhand des Feldtyps den passenden Resolver aus.
        """
        if field_name.endswith("_list") and issubclass(field_type, GeneralManager):
            return cls._createListResolver(
                lambda self: getattr(self, field_name), field_type
            )
        if issubclass(field_type, Measurement):
            return cls._createMeasurementResolver(field_name)
        return cls._createNormalResolver(field_name)

    @classmethod
    def _addQueriesToSchema(
        cls, graphene_type: type, generalManagerClass: GeneralManagerMeta
    ) -> None:
        """
        Add list and single-item query fields for a GeneralManager subclass to the GraphQL schema.
        
        Registers a list query field with optional filtering, exclusion, sorting, pagination, and grouping arguments, as well as a single-item query field using identification fields from the manager's interface. Attaches the corresponding resolvers for both queries to the schema.
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
        list_field = graphene.List(
            graphene_type,
            **attributes,
        )

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
        
        The generated mutation class filters out fields with `NOT_PROVIDED` values, calls the manager's `create` method with the provided arguments and the current user's ID, and returns a dictionary containing a success flag, any errors, and the created instance. Returns None if the manager class does not define an interface.
        
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
            Create a new instance of the manager class using the provided arguments.
            
            Filters out any fields with values set to `NOT_PROVIDED` before invoking the creation method. Returns a dictionary with a success flag, a list of errors if creation fails, and the created instance keyed by the manager class name.
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
                return {
                    "success": False,
                    "errors": [str(e)],
                }

            return {
                "success": True,
                "errors": [],
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
        
        The generated mutation accepts editable fields as arguments, calls the manager's `update` method with the provided values and the current user's ID, and returns a dictionary containing the operation's success status, any error messages, and the updated instance. Returns None if the manager class does not define an `Interface`.
        
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
            Updates a GeneralManager instance with the provided fields.
            
            Parameters:
                info (GraphQLResolveInfo): The GraphQL resolver context, typically containing user and request information.
                **kwargs: Fields to update, including the required 'id' of the instance.
            
            Returns:
                dict: A dictionary with keys 'success' (bool), 'errors' (list of str), and the updated instance keyed by its class name.
            """
            try:
                manager_id = kwargs.pop("id", None)
                instance = generalManagerClass(manager_id).update(
                    creator_id=info.context.user.id, **kwargs
                )
            except Exception as e:
                return {
                    "success": False,
                    "errors": [str(e)],
                }
            return {
                "success": True,
                "errors": [],
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
        
        The generated mutation accepts input fields defined by the manager's interface, deactivates the specified instance using its ID, and returns a dictionary containing the operation's success status, any error messages, and the deactivated instance keyed by the class name.
        
        Returns:
            The generated Graphene mutation class, or None if the manager class does not define an interface.
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
            Deactivates an instance of the specified GeneralManager class and returns the operation result.
            
            Returns:
                dict: A dictionary with keys:
                    - "success": Boolean indicating if the operation was successful.
                    - "errors": List of error messages, empty if successful.
                    - <ClassName>: The deactivated instance, keyed by the class name.
            """
            try:
                manager_id = kwargs.pop("id", None)
                instance = generalManagerClass(manager_id).deactivate(
                    creator_id=info.context.user.id
                )
            except Exception as e:
                return {
                    "success": False,
                    "errors": [str(e)],
                }
            return {
                "success": True,
                "errors": [],
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
