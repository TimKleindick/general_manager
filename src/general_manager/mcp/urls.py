"""URL routes for the GeneralManager MCP HTTP gateway."""

from django.urls import path

from general_manager.mcp.http_views import MCPGatewayChatView, MCPGatewayQueryView


urlpatterns = [
    path("ai/query", MCPGatewayQueryView.as_view(), name="general_manager_mcp_query"),
    path("ai/chat", MCPGatewayChatView.as_view(), name="general_manager_mcp_chat"),
]
