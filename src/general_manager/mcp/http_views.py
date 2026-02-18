"""HTTP adapter for the GeneralManager MCP gateway."""

from __future__ import annotations

import json
from typing import Any, Mapping

from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from general_manager.mcp.auth import MCPAuthError, build_context_from_http_request
from general_manager.mcp.ai_assistant import AIAssistant, MCPAIError
from general_manager.mcp.contract import (
    GatewayOperation,
    MCPGatewayValidationError,
    QueryRequest,
)
from general_manager.mcp.policy import MCPPolicyError
from general_manager.mcp.service import GatewayService
from general_manager.mcp.unanswered import UnansweredEvent, emit_unanswered_event


def _gateway_config() -> Mapping[str, Any]:
    config = getattr(settings, "GENERAL_MANAGER", {})
    if isinstance(config, Mapping):
        gateway = config.get("MCP_GATEWAY", {})
        if isinstance(gateway, Mapping):
            return gateway
    return {}


@method_decorator(csrf_exempt, name="dispatch")
class MCPGatewayQueryView(View):
    """Accepts structured query payloads and routes to gateway service."""

    http_method_names = ("post",)

    def post(self, request: HttpRequest) -> JsonResponse:
        try:
            payload = json.loads(request.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return JsonResponse(
                {
                    "data": {"rows": [], "aggregates": {}, "page_info": {}},
                    "provenance": {},
                    "errors": [
                        {
                            "code": "INVALID_JSON",
                            "message": "Request body must be valid JSON.",
                        }
                    ],
                },
                status=400,
            )

        try:
            context = build_context_from_http_request(request, _gateway_config())
        except MCPAuthError as exc:
            return JsonResponse(
                {
                    "data": {"rows": [], "aggregates": {}, "page_info": {}},
                    "provenance": {},
                    "errors": [{"code": exc.code, "message": exc.message}],
                },
                status=401,
            )

        try:
            query_request = QueryRequest.from_payload(payload)
        except MCPGatewayValidationError as exc:
            return JsonResponse(
                {
                    "data": {"rows": [], "aggregates": {}, "page_info": {}},
                    "provenance": {},
                    "errors": [{"code": "INVALID_REQUEST", "message": str(exc)}],
                },
                status=400,
            )

        service = GatewayService.from_settings()
        if query_request.operation is GatewayOperation.DISCOVER:
            response = service.discover_data_domains(context)
        elif query_request.operation is GatewayOperation.SCHEMA:
            response = service.describe_domain_schema(query_request.domain, context)
        elif query_request.operation is GatewayOperation.EXPLAIN:
            response = service.explain_query_plan(query_request, context)
        else:
            response = service.run_query(query_request, context)

        status = 200 if not response.errors else 403
        return JsonResponse(response.to_dict(), status=status)


@method_decorator(csrf_exempt, name="dispatch")
class MCPGatewayChatView(View):
    """Accepts human-language questions and delegates planning to AI assistant."""

    http_method_names = ("post",)

    def post(self, request: HttpRequest) -> JsonResponse:
        try:
            payload = json.loads(request.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return JsonResponse(
                {
                    "answer": "INVALID_JSON: Request body must be valid JSON.",
                    "errors": [
                        {
                            "code": "INVALID_JSON",
                            "message": "Request body must be valid JSON.",
                        }
                    ],
                },
                status=400,
            )

        question = payload.get("question") if isinstance(payload, dict) else None
        if not isinstance(question, str) or not question.strip():
            return JsonResponse(
                {
                    "answer": "INVALID_QUESTION: question must be a non-empty string.",
                    "errors": [
                        {
                            "code": "INVALID_QUESTION",
                            "message": "question must be a non-empty string.",
                        }
                    ],
                },
                status=400,
            )

        try:
            context = build_context_from_http_request(request, _gateway_config())
        except MCPAuthError as exc:
            return JsonResponse(
                {
                    "answer": f"{exc.code}: {exc.message}",
                    "errors": [{"code": exc.code, "message": exc.message}],
                },
                status=401,
            )

        assistant = AIAssistant.from_settings()
        gateway_config = _gateway_config()
        try:
            response = assistant.answer(question, context)
            gateway_payload = response.get("gateway_response")
            gateway_errors = (
                gateway_payload.get("errors", [])
                if isinstance(gateway_payload, dict)
                else []
            )
            if isinstance(gateway_errors, list) and gateway_errors:
                first = gateway_errors[0] if isinstance(gateway_errors[0], dict) else {}
                emit_unanswered_event(
                    UnansweredEvent(
                        context=context,
                        question=question,
                        reason_code=str(first.get("code", "GATEWAY_ERROR")),
                        reason_message=str(
                            first.get("message", "Gateway query failed.")
                        ),
                        query_request=response.get("query_request"),
                        gateway_response=gateway_payload,
                        answer=response.get("answer"),
                    ),
                    gateway_config,
                )
            return JsonResponse(response, status=200)
        except (MCPAIError, MCPPolicyError) as exc:
            emit_unanswered_event(
                UnansweredEvent(
                    context=context,
                    question=question,
                    reason_code=getattr(exc, "code", "AI_ERROR"),
                    reason_message=str(exc),
                ),
                gateway_config,
            )
            return JsonResponse(
                {
                    "answer": f"{getattr(exc, 'code', 'AI_ERROR')}: {exc!s}",
                    "errors": [
                        {
                            "code": getattr(exc, "code", "AI_ERROR"),
                            "message": str(exc),
                        }
                    ],
                },
                status=400,
            )
