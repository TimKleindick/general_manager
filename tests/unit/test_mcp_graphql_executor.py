from general_manager.mcp.contract import QueryRequest
from general_manager.mcp.graphql_executor import GraphQLTemplateExecutor
from general_manager.mcp.policy import DomainPolicy


def test_compile_query_generates_fixed_template() -> None:
    executor = GraphQLTemplateExecutor()
    request = QueryRequest.from_payload(
        {
            "domain": "Project",
            "operation": "query",
            "select": ["id", "name", "status"],
            "filters": [{"field": "name", "op": "contains", "value": "Nova"}],
            "page": 1,
            "page_size": 20,
        }
    )
    policy = DomainPolicy(
        domain="Project",
        manager_name="Project",
        readable_fields={"id", "name", "status"},
        filterable_fields={"name"},
        sortable_fields={"name"},
        aggregate_fields={"budget"},
    )

    compiled = executor.compile_query(request, policy, request.select)

    assert compiled.template_name == "project_list"
    assert "projectList" in compiled.query
    assert "filter:" in compiled.query
    assert "name_Contains" in compiled.query
    assert "pageSize: 20" in compiled.query
