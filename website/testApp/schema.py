# schema.py
import graphene
from .models import Project

from django.db.models import Q
import json


def build_dynamic_filter(filter_dict):
    filter_kwargs = {}

    for key, value in filter_dict.items():
        if isinstance(value, dict):
            # Rekursiv für verschachtelte Filterbedingungen
            raise NotImplementedError
        else:
            # Direkter Filter auf dem Modell
            filter_kwargs[key] = value

    return filter_kwargs


class ProjectType(graphene.ObjectType):
    id = graphene.ID()
    name = graphene.String()
    start_date = graphene.Date()
    end_date = graphene.Date()
    total_capex = graphene.String()

    def resolve_id(self, info):
        return self.id

    def resolve_name(self, info):
        return self.name

    def resolve_start_date(self, info):
        return self.start_date

    def resolve_end_date(self, info):
        return self.end_date

    def resolve_total_capex(self, info):
        return self.total_capex


class Query(graphene.ObjectType):
    projects = graphene.List(
        ProjectType,
        filter=graphene.JSONString(),
    )
    project = graphene.Field(ProjectType, id=graphene.Int(required=True))

    def resolve_projects(self, info, filter=None):
        queryset = Project.all()

        # Dynamische Filter anwenden
        if filter:
            filter_dict = json.loads(filter) if isinstance(filter, str) else filter
            dynamic_filter = build_dynamic_filter(filter_dict)
            queryset = queryset.filter(**dynamic_filter)

        return queryset

    def resolve_project(self, info, id):
        return Project(id)


schema = graphene.Schema(query=Query)
