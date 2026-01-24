"""Meilisearch backend adapter."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from general_manager.search.backend import (
    SearchBackendClientMissingError,
    SearchBackendError,
    SearchDocument,
    SearchHit,
    SearchResult,
)


class MeilisearchBackend:
    """Meilisearch implementation of the SearchBackend protocol."""

    def __init__(
        self,
        url: str = "http://127.0.0.1:7700",
        api_key: str | None = None,
        client: Any | None = None,
    ) -> None:
        if client is None:
            try:
                import meilisearch  # type: ignore[import]
            except ImportError as exc:
                raise SearchBackendClientMissingError("Meilisearch") from exc
            client = meilisearch.Client(url, api_key)
        self._client = client

    def ensure_index(self, index_name: str, settings: Mapping[str, Any]) -> None:
        index = self._get_or_create_index(index_name)
        searchable_fields = settings.get("searchable_fields", [])
        filterable_fields = settings.get("filterable_fields", [])
        sortable_fields = settings.get("sortable_fields", [])
        if searchable_fields:
            task = index.update_settings(
                {"searchableAttributes": list(searchable_fields)}
            )
            self._wait_for_task(task)
        if filterable_fields:
            task = index.update_settings(
                {"filterableAttributes": list(filterable_fields)}
            )
            self._wait_for_task(task)
        if sortable_fields:
            task = index.update_settings({"sortableAttributes": list(sortable_fields)})
            self._wait_for_task(task)

    def upsert(self, index_name: str, documents: Sequence[SearchDocument]) -> None:
        index = self._get_or_create_index(index_name)
        payload = [self._document_payload(doc) for doc in documents]
        if payload:
            task = index.add_documents(payload)
            self._wait_for_task(task)

    def delete(self, index_name: str, ids: Sequence[str]) -> None:
        index = self._get_or_create_index(index_name)
        if ids:
            task = index.delete_documents(list(ids))
            self._wait_for_task(task)

    def search(
        self,
        index_name: str,
        query: str,
        *,
        filters: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
        filter_expression: str | None = None,
        sort_by: str | None = None,
        sort_desc: bool = False,
        limit: int = 10,
        offset: int = 0,
        types: Sequence[str] | None = None,
    ) -> SearchResult:
        index = self._get_or_create_index(index_name)
        payload: dict[str, Any] = {
            "limit": limit,
            "offset": offset,
        }
        filter_expr = filter_expression or self._build_filter_expression(filters, types)
        if filter_expr:
            payload["filter"] = filter_expr
        if sort_by:
            direction = "desc" if sort_desc else "asc"
            payload["sort"] = [f"{sort_by}:{direction}"]

        response = index.search(query, payload)
        hits = [
            SearchHit(
                id=hit.get("id"),
                type=hit.get("type"),
                identification=hit.get("identification", {}),
                score=hit.get("_rankingScore"),
                index=index_name,
                data=hit.get("data", {}),
            )
            for hit in response.get("hits", [])
        ]
        return SearchResult(
            hits=hits,
            total=response.get("estimatedTotalHits", len(hits)),
            took_ms=response.get("processingTimeMs"),
            raw=response,
        )

    def _get_or_create_index(self, index_name: str) -> Any:
        try:
            return self._client.get_index(index_name)
        except Exception:  # noqa: BLE001
            task = self._client.create_index(index_name, {"primaryKey": "id"})
            self._wait_for_task(task)
            return self._client.get_index(index_name)

    @staticmethod
    def _document_payload(document: SearchDocument) -> dict[str, Any]:
        return {
            "id": document.id,
            "type": document.type,
            "identification": document.identification,
            "data": document.data,
            **document.data,
        }

    @staticmethod
    def _build_filter_expression(
        filters: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None,
        types: Sequence[str] | None,
    ) -> str | None:
        clauses: list[str] = []
        if types:
            type_clause = " OR ".join([f'type = "{type_name}"' for type_name in types])
            clauses.append(f"({type_clause})")
        if filters:
            filter_groups = filters if isinstance(filters, (list, tuple)) else [filters]
            group_clauses: list[str] = []
            for group in filter_groups:
                parts: list[str] = []
                for key, value in group.items():
                    if "__" in key:
                        field_name, lookup = key.split("__", 1)
                    else:
                        field_name, lookup = key, "exact"
                    if lookup == "in" and isinstance(value, (list, tuple, set)):
                        options = " OR ".join(
                            [f'{field_name} = "{item}"' for item in value]
                        )
                        parts.append(f"({options})")
                        continue
                    if isinstance(value, (list, tuple, set)):
                        options = " OR ".join(
                            [f'{field_name} = "{item}"' for item in value]
                        )
                        parts.append(f"({options})")
                    else:
                        parts.append(f'{field_name} = "{value}"')
                if parts:
                    group_clauses.append(" AND ".join(parts))
            if group_clauses:
                clauses.append(" OR ".join(f"({clause})" for clause in group_clauses))
        if not clauses:
            return None
        return " AND ".join(clauses)

    def _wait_for_task(self, task: Any) -> None:
        task_uid = self._extract_task_uid(task)
        if task_uid is None:
            return
        wait_for_task = getattr(self._client, "wait_for_task", None)
        if callable(wait_for_task):
            result = wait_for_task(task_uid)
            self._raise_for_failed_task(result)
            return
        get_task = getattr(self._client, "get_task", None)
        if callable(get_task):
            result = get_task(task_uid)
            self._raise_for_failed_task(result)

    @staticmethod
    def _extract_task_uid(task: Any) -> str | None:
        if task is None:
            return None
        if isinstance(task, Mapping):
            return (
                task.get("taskUid")
                or task.get("task_uid")
                or task.get("uid")
                or task.get("taskId")
            )
        for name in ("task_uid", "taskUid", "uid", "task_id"):
            value = getattr(task, name, None)
            if value is not None:
                return value
        return None

    @staticmethod
    def _raise_for_failed_task(result: Any) -> None:
        if result is None:
            return
        if isinstance(result, Mapping):
            status = result.get("status")
            error = result.get("error")
        else:
            status = getattr(result, "status", None)
            error = getattr(result, "error", None)
        if status and status != "succeeded":
            raise MeilisearchTaskFailedError(status, error)


class MeilisearchTaskFailedError(SearchBackendError):
    """Raised when a Meilisearch task fails to complete successfully."""

    def __init__(self, status: str | None, error: Any | None) -> None:
        super().__init__(
            f"Meilisearch task did not succeed (status={status}, error={error})."
        )
