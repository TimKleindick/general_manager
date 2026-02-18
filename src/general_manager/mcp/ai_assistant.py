"""AI assistant orchestration for human-language questions over MCP gateway."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
from typing import Any, Mapping, Protocol
from urllib import error, request

from django.conf import settings

from general_manager.logging import get_logger
from general_manager.mcp.contract import (
    GatewayOperation,
    MCPGatewayValidationError,
    QueryContext,
    QueryRequest,
)
from general_manager.mcp.service import GatewayService


logger = get_logger("mcp.ai_assistant")


class MCPAIError(RuntimeError):
    """Raised when AI planning fails."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class Planner(Protocol):
    """Planner protocol mapping user question to a structured request."""

    def plan(self, question: str, domains: list[dict[str, Any]]) -> dict[str, Any]:
        """Build MCP query payload from human question."""


class AnswerSynthesizer(Protocol):
    """Synthesizer protocol for final human-facing answers."""

    def synthesize(
        self,
        question: str,
        query_request: dict[str, Any],
        gateway_payload: dict[str, Any],
    ) -> str:
        """Build final assistant response text."""


class RuleBasedPlanner:
    """Deterministic fallback planner used when LLM is unavailable."""

    def plan(self, question: str, _domains: list[dict[str, Any]]) -> dict[str, Any]:
        normalized = question.lower()
        quoted = re.search(r'"([^"]+)"', question)
        quoted_value = quoted.group(1).strip() if quoted else None

        if "domain" in normalized or "schema" in normalized or "what can" in normalized:
            return {
                "domain": "Project",
                "operation": "discover",
                "select": ["id"],
                "filters": [],
                "sort": [],
                "page": 1,
                "page_size": 1,
                "group_by": [],
                "metrics": [],
            }

        if "count" in normalized or "how many" in normalized:
            return {
                "domain": "Project",
                "operation": "aggregate",
                "select": ["id"],
                "filters": [],
                "sort": [],
                "page": 1,
                "page_size": 50,
                "group_by": [],
                "metrics": [{"field": "id", "op": "count", "alias": "project_count"}],
            }

        if "sum" in normalized and "volume" in normalized:
            return {
                "domain": "Project",
                "operation": "aggregate",
                "select": ["id", "total_volume"],
                "filters": [],
                "sort": [],
                "page": 1,
                "page_size": 50,
                "group_by": [],
                "metrics": [
                    {
                        "field": "total_volume",
                        "op": "sum",
                        "alias": "total_volume_sum",
                    }
                ],
            }

        filters = (
            [{"field": "name", "op": "contains", "value": quoted_value}]
            if quoted_value
            else []
        )
        return {
            "domain": "Project",
            "operation": "query",
            "select": [
                "id",
                "name",
                "project_phase_type_name",
                "total_volume",
                "earliest_sop",
                "latest_eop",
            ],
            "filters": filters,
            "sort": [{"field": "name", "direction": "asc"}],
            "page": 1,
            "page_size": 10,
            "group_by": [],
            "metrics": [],
        }


class OpenAICompatiblePlanner:
    """LLM planner using an OpenAI-compatible responses endpoint."""

    def __init__(self, config: Mapping[str, Any]) -> None:
        self.endpoint = str(
            config.get("ENDPOINT", "https://api.openai.com/v1/responses")
        )
        self.model = str(config.get("MODEL", "gpt-4.1-mini"))
        self.temperature = float(config.get("TEMPERATURE", 0))
        self.api_key_env = str(config.get("API_KEY_ENV", "OPENAI_API_KEY"))
        self.system_prompt = str(
            config.get(
                "SYSTEM_PROMPT",
                (
                    "You are a planning engine. Convert user questions into one strict JSON object "
                    "with keys: domain, operation, select, filters, sort, page, page_size, group_by, metrics. "
                    "Allowed operation: query|aggregate|schema|discover|explain. "
                    "Return JSON only, no markdown."
                ),
            )
        )
        self.repair_system_prompt = str(
            config.get(
                "REPAIR_SYSTEM_PROMPT",
                (
                    "You repair invalid MCP query plans. Return one strict JSON object "
                    "with exactly these keys: domain, operation, select, filters, sort, "
                    "page, page_size, group_by, metrics. "
                    "Allowed operation: query|aggregate|schema|discover|explain. "
                    "Filters require: field, op, value. Sort requires: field, direction. "
                    "Metrics require: field, op, optional alias. "
                    "Use only operators supported by the schema context. "
                    "Return JSON only, no markdown."
                ),
            )
        )

    def plan(self, question: str, domains: list[dict[str, Any]]) -> dict[str, Any]:
        planner_prompt = self.system_prompt
        user_prompt = (
            f"Domains: {json.dumps(domains)}\n"
            f"Question: {question}\n"
            "Return only a single JSON object."
        )
        text = self._call_llm(
            api_key_env=self.api_key_env,
            endpoint=self.endpoint,
            model=self.model,
            temperature=self.temperature,
            system_prompt=planner_prompt,
            user_prompt=user_prompt,
        )
        plan_payload = self._extract_json(text)
        if not isinstance(plan_payload, dict):
            raise MCPAIError("AI_INVALID_PLAN", "Planner did not return a JSON object.")
        return plan_payload

    def repair_plan(
        self,
        *,
        question: str,
        domains: list[dict[str, Any]],
        invalid_plan: dict[str, Any],
        validation_error: str,
    ) -> dict[str, Any]:
        user_prompt = (
            f"Domains: {json.dumps(domains)}\n"
            f"Question: {question}\n"
            f"Invalid Plan: {json.dumps(invalid_plan)}\n"
            f"Validation Error: {validation_error}\n"
            "Repair the plan so it validates. Return only a single JSON object."
        )
        text = self._call_llm(
            api_key_env=self.api_key_env,
            endpoint=self.endpoint,
            model=self.model,
            temperature=self.temperature,
            system_prompt=self.repair_system_prompt,
            user_prompt=user_prompt,
        )
        repaired_payload = self._extract_json(text)
        if not isinstance(repaired_payload, dict):
            raise MCPAIError("AI_INVALID_PLAN", "Repair planner did not return JSON.")
        return repaired_payload

    @staticmethod
    def _call_llm(
        *,
        api_key_env: str,
        endpoint: str,
        model: str,
        temperature: float,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        api_key = os.environ.get(api_key_env)
        if not api_key:
            raise MCPAIError(
                "AI_NOT_CONFIGURED",
                f"Missing API key env var: {api_key_env}",
            )

        if endpoint.rstrip("/").endswith("/chat/completions"):
            payload = {
                "model": model,
                "temperature": temperature,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            }
        else:
            payload = {
                "model": model,
                "temperature": temperature,
                "input": [
                    {
                        "role": "system",
                        "content": [{"type": "input_text", "text": system_prompt}],
                    },
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": user_prompt}],
                    },
                ],
            }
        req = request.Request(  # noqa: S310
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )

        try:
            with request.urlopen(req, timeout=30) as response:  # noqa: S310
                raw = response.read().decode("utf-8")
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise MCPAIError(
                "AI_HTTP_ERROR", f"Planner request failed with HTTP {exc.code}: {body}"
            ) from exc
        except Exception as exc:
            raise MCPAIError(
                "AI_REQUEST_ERROR", f"Planner request failed: {type(exc).__name__}"
            ) from exc

        response_payload = json.loads(raw)
        text = OpenAICompatiblePlanner._extract_text(response_payload)
        if not text:
            raise MCPAIError("AI_EMPTY_RESPONSE", "Planner returned no text.")
        return text

    @staticmethod
    def _extract_text(payload: dict[str, Any]) -> str:
        output_text = payload.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text.strip()

        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            message = choices[0].get("message", {})
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
            if isinstance(content, list):
                chunks = [
                    chunk.get("text", "")
                    for chunk in content
                    if isinstance(chunk, dict)
                ]
                text = "\n".join(item for item in chunks if item)
                if text.strip():
                    return text.strip()

        output = payload.get("output")
        if isinstance(output, list):
            texts: list[str] = []
            for item in output:
                if not isinstance(item, dict):
                    continue
                for content in item.get("content", []) or []:
                    if not isinstance(content, dict):
                        continue
                    chunk_text = content.get("text")
                    if isinstance(chunk_text, str) and chunk_text:
                        texts.append(chunk_text)
            if texts:
                return "\n".join(texts).strip()

        return ""

    @staticmethod
    def _extract_json(text: str) -> Any:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
            cleaned = re.sub(r"```$", "", cleaned).strip()

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if not match:
                raise
            return json.loads(match.group(0))


@dataclass(slots=True)
class AIAssistant:
    """Executes human-language questions through planner + gateway."""

    service: GatewayService
    planner: Planner
    synthesizer: AnswerSynthesizer

    @classmethod
    def from_settings(cls) -> "AIAssistant":
        service = GatewayService.from_settings()
        config = _assistant_config()
        planner_mode = str(config.get("PLANNER", "rule_based"))
        if planner_mode == "openai_compatible":
            planner: Planner = OpenAICompatiblePlanner(config)
        else:
            planner = RuleBasedPlanner()
        synthesizer_mode = str(
            config.get(
                "SYNTHESIZER",
                "openai_compatible"
                if planner_mode == "openai_compatible"
                else "rule_based",
            )
        )
        if synthesizer_mode == "openai_compatible":
            synthesizer: AnswerSynthesizer = OpenAICompatibleAnswerSynthesizer(config)
        else:
            synthesizer = RuleBasedAnswerSynthesizer()
        return cls(service=service, planner=planner, synthesizer=synthesizer)

    def answer(self, question: str, context: QueryContext) -> dict[str, Any]:
        if not question.strip():
            raise MCPAIError("INVALID_QUESTION", "question must be a non-empty string")

        domains = self.service.policy_engine.discover_domains()
        planned_payload = self.planner.plan(question, domains)
        planned_payload = self._normalize_plan_payload(question, planned_payload)

        try:
            query_request = QueryRequest.from_payload(planned_payload)
        except MCPGatewayValidationError as exc:
            repaired_payload = self._attempt_plan_repair(
                question=question,
                domains=domains,
                invalid_payload=planned_payload,
                validation_error=str(exc),
            )
            if repaired_payload is None:
                raise MCPAIError("AI_INVALID_PLAN", str(exc)) from exc
            planned_payload = repaired_payload
            try:
                query_request = QueryRequest.from_payload(planned_payload)
            except MCPGatewayValidationError as second_exc:
                raise MCPAIError("AI_INVALID_PLAN", str(second_exc)) from second_exc

        if query_request.operation is GatewayOperation.DISCOVER:
            gateway_response = self.service.discover_data_domains(context)
        elif query_request.operation is GatewayOperation.SCHEMA:
            gateway_response = self.service.describe_domain_schema(
                query_request.domain, context
            )
        elif query_request.operation is GatewayOperation.EXPLAIN:
            gateway_response = self.service.explain_query_plan(query_request, context)
        else:
            gateway_response = self.service.run_query(query_request, context)

        gateway_payload = gateway_response.to_dict()
        answer = self._synthesize_answer(question, planned_payload, gateway_payload)
        return {
            "answer": answer,
            "question": question,
            "query_request": planned_payload,
            "gateway_response": gateway_payload,
        }

    def _attempt_plan_repair(
        self,
        *,
        question: str,
        domains: list[dict[str, Any]],
        invalid_payload: dict[str, Any],
        validation_error: str,
    ) -> dict[str, Any] | None:
        repair_fn = getattr(self.planner, "repair_plan", None)
        if not callable(repair_fn):
            return None
        try:
            repaired = repair_fn(
                question=question,
                domains=domains,
                invalid_plan=invalid_payload,
                validation_error=validation_error,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "planner repair failed",
                context={"error": type(exc).__name__, "message": str(exc)},
            )
            return None
        if not isinstance(repaired, dict):
            return None
        return self._normalize_plan_payload(question, repaired)

    @staticmethod
    def _normalize_plan_payload(
        question: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return payload

        normalized = dict(payload)
        domain = normalized.get("domain")
        operation = normalized.get("operation")

        filters = normalized.get("filters")
        if isinstance(filters, list):
            fixed_filters: list[dict[str, Any]] = []
            derivative_filter_detected = False
            for raw_filter in filters:
                if not isinstance(raw_filter, dict):
                    fixed_filters.append(raw_filter)
                    continue
                item = dict(raw_filter)
                if "op" not in item and "operator" in item:
                    item["op"] = item.get("operator")
                field = item.get("field")
                if isinstance(field, str):
                    field_key = field.strip().lower().replace(".", "_")
                    if field_key in {
                        "derivatives",
                        "derivative",
                        "derivative_id",
                        "derivativeid",
                        "derivative_count",
                        "derivativecount",
                    }:
                        item["field"] = "derivative_count"
                        derivative_filter_detected = True
                fixed_filters.append(item)
            normalized["filters"] = fixed_filters

            question_lc = question.lower()
            if (
                isinstance(domain, str)
                and domain == "Project"
                and isinstance(operation, str)
                and operation == "aggregate"
                and derivative_filter_detected
                and (
                    "how many" in question_lc
                    or "number of" in question_lc
                    or "wie viele" in question_lc
                    or "anzahl" in question_lc
                )
            ):
                normalized["operation"] = "query"
                normalized["select"] = ["id", "name", "derivative_count"]
                normalized["group_by"] = []
                normalized["metrics"] = []
                normalized["sort"] = [
                    {"field": "derivative_count", "direction": "desc"}
                ]
                normalized["page"] = 1
                normalized["page_size"] = 1

        metrics = normalized.get("metrics")
        if isinstance(metrics, list):
            fixed_metrics: list[dict[str, Any]] = []
            for raw_metric in metrics:
                if not isinstance(raw_metric, dict):
                    fixed_metrics.append(raw_metric)
                    continue
                item = dict(raw_metric)
                if "op" not in item and "aggregate_function" in item:
                    item["op"] = item.get("aggregate_function")
                # LLMs occasionally omit metric operators for count-like prompts.
                # Default to count so validation does not fail hard.
                if "op" not in item or item.get("op") in (None, ""):
                    item["op"] = "count"
                fixed_metrics.append(item)
            normalized["metrics"] = fixed_metrics

        return normalized

    def _synthesize_answer(
        self,
        question: str,
        query_request: dict[str, Any],
        gateway_payload: dict[str, Any],
    ) -> str:
        try:
            return self.synthesizer.synthesize(question, query_request, gateway_payload)
        except MCPAIError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ai synthesizer failed; falling back",
                context={"error": type(exc).__name__, "message": str(exc)},
            )
            return RuleBasedAnswerSynthesizer().synthesize(
                question, query_request, gateway_payload
            )


class RuleBasedAnswerSynthesizer:
    """Deterministic fallback answer generator."""

    def synthesize(
        self,
        question: str,
        query_request: dict[str, Any],
        gateway_payload: dict[str, Any],
    ) -> str:
        del question
        del query_request
        return _format_answer(gateway_payload)


class OpenAICompatibleAnswerSynthesizer:
    """LLM answer generator grounded in gateway results."""

    def __init__(self, config: Mapping[str, Any]) -> None:
        self.endpoint = str(
            config.get("ENDPOINT", "https://api.openai.com/v1/responses")
        )
        self.model = str(
            config.get("ANSWER_MODEL", config.get("MODEL", "gpt-4.1-mini"))
        )
        self.temperature = float(config.get("ANSWER_TEMPERATURE", 0.1))
        self.api_key_env = str(config.get("API_KEY_ENV", "OPENAI_API_KEY"))
        self.system_prompt = str(
            config.get(
                "ANSWER_SYSTEM_PROMPT",
                (
                    "You are a data analyst assistant. Answer clearly and concisely using only "
                    "the provided structured result. If there are errors, explain them plainly. "
                    "Do not invent facts not present in the result."
                ),
            )
        )

    def synthesize(
        self,
        question: str,
        query_request: dict[str, Any],
        gateway_payload: dict[str, Any],
    ) -> str:
        compact_payload = self._compact_payload(gateway_payload)
        user_prompt = (
            f"Question: {question}\n"
            f"Query Request: {json.dumps(query_request)}\n"
            f"Gateway Result: {json.dumps(compact_payload)}\n"
            "Write a concise answer for a human user."
        )
        try:
            return OpenAICompatiblePlanner._call_llm(
                api_key_env=self.api_key_env,
                endpoint=self.endpoint,
                model=self.model,
                temperature=self.temperature,
                system_prompt=self.system_prompt,
                user_prompt=user_prompt,
            )
        except MCPAIError as exc:
            logger.warning(
                "answer synthesizer call failed; using rule-based fallback",
                context={"code": exc.code, "message": exc.message},
            )
            return RuleBasedAnswerSynthesizer().synthesize(
                question, query_request, gateway_payload
            )

    @staticmethod
    def _compact_payload(gateway_payload: dict[str, Any]) -> dict[str, Any]:
        data = gateway_payload.get("data", {})
        compact_data: dict[str, Any] = {}
        if isinstance(data, dict):
            rows = data.get("rows")
            compact_data["rows"] = rows[:5] if isinstance(rows, list) else []
            compact_data["aggregates"] = data.get("aggregates", {})
            compact_data["page_info"] = data.get("page_info", {})
            compact_data["domains"] = data.get("domains", [])
            compact_data["schema"] = data.get("schema", {})
        return {
            "data": compact_data,
            "errors": gateway_payload.get("errors", []),
            "provenance": gateway_payload.get("provenance", {}),
        }


def _assistant_config() -> Mapping[str, Any]:
    config = getattr(settings, "GENERAL_MANAGER", {})
    if isinstance(config, Mapping):
        gateway = config.get("MCP_GATEWAY", {})
        if isinstance(gateway, Mapping):
            assistant = gateway.get("AI_ASSISTANT", {})
            if isinstance(assistant, Mapping):
                return assistant
    return {}


def _format_answer(payload: dict[str, Any]) -> str:
    errors = payload.get("errors", [])
    if isinstance(errors, list) and errors:
        first = errors[0] if isinstance(errors[0], dict) else {}
        code = first.get("code", "ERROR")
        message = first.get("message", "Request failed")
        return f"{code}: {message}"

    data = payload.get("data", {}) if isinstance(payload.get("data"), dict) else {}
    domains = data.get("domains")
    if isinstance(domains, list) and domains:
        names = [str(item.get("domain")) for item in domains if isinstance(item, dict)]
        return f"Available domains: {', '.join(names)}"

    aggregates = data.get("aggregates")
    if isinstance(aggregates, dict) and aggregates:
        return "\n".join(f"{key}: {value}" for key, value in aggregates.items())

    rows = data.get("rows")
    if isinstance(rows, list) and rows:
        preview: list[str] = []
        for item in rows[:5]:
            if not isinstance(item, dict):
                continue
            row_id = item.get("id", "-")
            row_name = item.get("name", "-")
            preview.append(f"#{row_id} {row_name}")
        if preview:
            return "\n".join(preview)

    return "No results found for this question."
