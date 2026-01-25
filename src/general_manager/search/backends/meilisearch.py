"""Meilisearch backend adapter."""

from __future__ import annotations

import hashlib
import re
import time
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

    _ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,511}$")

    def __init__(
        self,
        url: str = "http://127.0.0.1:7700",
        api_key: str | None = None,
        client: Any | None = None,
    ) -> None:
        """
        Initialize the backend with either a provided Meilisearch client or a newly created one.

        Parameters:
            url (str): Base URL to use when creating a Meilisearch client if `client` is not provided.
            api_key (str | None): Optional API key to use when creating the client.
            client (Any | None): Optional preconfigured Meilisearch client instance to use directly. If omitted, the constructor will import the `meilisearch` package and instantiate a client with `url` and `api_key`; if the package is not available, raises SearchBackendClientMissingError("Meilisearch").
        """
        if client is None:
            try:
                import meilisearch  # type: ignore[import]
            except ImportError as exc:
                raise SearchBackendClientMissingError("Meilisearch") from exc
            client = meilisearch.Client(url, api_key)
        self._client = client

    def ensure_index(self, index_name: str, settings: Mapping[str, Any]) -> None:
        """
        Ensure a Meilisearch index with the given name exists and apply searchable, filterable, and sortable field settings.

        Parameters:
            index_name (str): Name of the index to retrieve or create.
            settings (Mapping[str, Any]): Index settings mapping. Recognized keys:
                - "searchable_fields": iterable of field names to set as searchableAttributes.
                - "filterable_fields": iterable of field names to set as filterableAttributes.
                - "sortable_fields": iterable of field names to set as sortableAttributes.

        This method will create the index if it does not exist and wait for each settings update task to complete before returning.
        """
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
        """
        Ensure the index exists, then index or update the given documents and wait for the indexing task to complete.

        Parameters:
                index_name (str): Name of the Meilisearch index to upsert documents into.
                documents (Sequence[SearchDocument]): Sequence of documents to add or update in the index.

        Raises:
                MeilisearchTaskFailedError: If the Meilisearch task completes with a failed or canceled status.
        """
        index = self._get_or_create_index(index_name)
        payload = [self._document_payload(doc) for doc in documents]
        if payload:
            task = index.add_documents(payload)
            self._wait_for_task(task)

    def delete(self, index_name: str, ids: Sequence[str]) -> None:
        """
        Delete documents from the specified index by their IDs.

        Parameters:
                index_name (str): Name of the index to delete documents from.
                ids (Sequence[str]): Sequence of document IDs to remove; each ID will be normalized before deletion. If empty, no action is taken.

        Raises:
                MeilisearchTaskFailedError: If the backend reports the deletion task failed or was canceled.
        """
        index = self._get_or_create_index(index_name)
        if ids:
            normalized_ids = [self._normalize_document_id(doc_id) for doc_id in ids]
            task = index.delete_documents(normalized_ids)
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
        """
        Execute a search against the specified Meilisearch index using query, optional filters, sorting, and pagination.

        Parameters:
            index_name (str): Name of the index to search.
            query (str): Full-text query string.
            filters (Mapping[str, Any] | Sequence[Mapping[str, Any]] | None): Field-based filter(s). May be a single mapping or a sequence of mappings representing OR groups; keys may include nested lookups (e.g., "field__lookup").
            filter_expression (str | None): Raw Meilisearch filter expression to use instead of `filters`.
            sort_by (str | None): Field name to sort results by.
            sort_desc (bool): If true, sort in descending order; otherwise ascending.
            limit (int): Maximum number of results to return.
            offset (int): Number of results to skip.
            types (Sequence[str] | None): Sequence of document type names to restrict results to the `type` field.

        Returns:
            SearchResult: Object containing matched hits, total hits estimate, request processing time in milliseconds, and the raw Meilisearch response.
        """
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
                id=hit.get("gm_document_id", hit.get("id")),
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
        """
        Ensure a Meilisearch index with the given name exists and return it.

        If the index does not exist, create it with primary key "id" and wait for the creation task to complete.

        Returns:
            The Meilisearch index object for the given index name.
        """
        try:
            return self._client.get_index(index_name)
        except Exception:  # noqa: BLE001
            task = self._client.create_index(index_name, {"primaryKey": "id"})
            self._wait_for_task(task)
            return self._client.get_index(index_name)

    @staticmethod
    def _document_payload(document: SearchDocument) -> dict[str, Any]:
        """
        Build a Meilisearch-ready document payload from a SearchDocument.

        Parameters:
            document (SearchDocument): The source document whose fields and data will be mapped into the payload.

        Returns:
            dict[str, Any]: A dictionary containing:
                - `id`: normalized document id suitable for Meilisearch,
                - `gm_document_id`: the original document id,
                - `type`: the document type,
                - `identification`: the document identification value,
                - `data`: the original data mapping,
                - additional top-level keys copied from `document.data` except reserved keys (`id`, `gm_document_id`, `type`, `identification`, `data`).
        """
        reserved_keys = {"id", "gm_document_id", "type", "identification", "data"}
        extra_data = {
            key: value
            for key, value in document.data.items()
            if key not in reserved_keys
        }
        return {
            "id": MeilisearchBackend._normalize_document_id(document.id),
            "gm_document_id": document.id,
            "type": document.type,
            "identification": document.identification,
            "data": document.data,
            **extra_data,
        }

    @staticmethod
    def _build_filter_expression(
        filters: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None,
        types: Sequence[str] | None,
    ) -> str | None:
        """
        Builds a Meilisearch-compatible filter expression from the given filters and types.

        Parameters:
            filters (Mapping[str, Any] | Sequence[Mapping[str, Any]] | None):
                A single filter mapping or a sequence of filter mappings.
                Each mapping's keys are field names or lookups using `field__lookup`.
                - Keys without `__` use exact equality.
                - `__in` or a list/tuple/set value creates an OR group for that field.
                Multiple fields in one mapping are combined with AND; multiple mappings are combined with OR.
            types (Sequence[str] | None):
                Sequence of type names to restrict results to; these are combined with OR against the `type` field.

        Returns:
            str | None: A Meilisearch filter expression string, or `None` if no clauses were produced.
        """
        clauses: list[str] = []
        if types:
            type_clause = " OR ".join(
                [f'type = "{_escape_filter_value(type_name)}"' for type_name in types]
            )
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
                            [
                                f'{field_name} = "{_escape_filter_value(item)}"'
                                for item in value
                            ]
                        )
                        parts.append(f"({options})")
                        continue
                    if isinstance(value, (list, tuple, set)):
                        options = " OR ".join(
                            [
                                f'{field_name} = "{_escape_filter_value(item)}"'
                                for item in value
                            ]
                        )
                        parts.append(f"({options})")
                    else:
                        parts.append(f'{field_name} = "{_escape_filter_value(value)}"')
                if parts:
                    group_clauses.append(" AND ".join(parts))
            if group_clauses:
                clauses.append(" OR ".join(f"({clause})" for clause in group_clauses))
        if not clauses:
            return None
        return " AND ".join(clauses)

    def _wait_for_task(self, task: Any) -> None:
        """
        Waits for a Meilisearch task to complete using the backend client.

        Parameters:
            task (Any): A task object or task-like response from which a task UID will be extracted.

        Raises:
            MeilisearchTaskFailedError: If the task finished with status "failed" or "canceled".
        """
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
            timeout_seconds = 5.0
            poll_interval = 0.1
            start = time.monotonic()
            while True:
                result = get_task(task_uid)
                status = self._extract_task_status(result)
                if status in {"succeeded", "failed", "canceled"}:
                    self._raise_for_failed_task(result)
                    return
                if time.monotonic() - start >= timeout_seconds:
                    return
                time.sleep(poll_interval)
                poll_interval = min(poll_interval * 1.5, 1.0)

    @staticmethod
    def _extract_task_uid(task: Any) -> str | None:
        """
        Extract the Meilisearch task UID from a mapping or object.

        Checks common key names ("taskUid", "task_uid", "uid", "taskId") when `task` is a mapping,
        or the corresponding attribute names ("task_uid", "taskUid", "uid", "task_id") when `task`
        is an object, and returns the first matching value found.

        Parameters:
            task (Any): A task mapping or object from which to extract the UID.

        Returns:
            str | None: The extracted task UID if present, otherwise `None`.
        """
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
    def _extract_task_status(result: Any) -> str | None:
        if result is None:
            return None
        if isinstance(result, Mapping):
            status = result.get("status")
        else:
            status = getattr(result, "status", None)
        return str(status).lower() if status is not None else None

    @staticmethod
    def _raise_for_failed_task(result: Any) -> None:
        """
        Raise MeilisearchTaskFailedError when a Meilisearch task result indicates failure or cancellation.

        Parameters:
            result (Any): A task result object or mapping. Accepted shapes:
                - Mapping with keys "status" and "error"
                - Object with attributes `status` and `error`
                If `result` is None, the function does nothing.

        Raises:
            MeilisearchTaskFailedError: If the task `status` (case-insensitive) is "failed" or "canceled"; the exception is constructed with the observed status and error.
        """
        if result is None:
            return
        if isinstance(result, Mapping):
            status = result.get("status")
            error = result.get("error")
        else:
            status = getattr(result, "status", None)
            error = getattr(result, "error", None)
        normalized_status = str(status).lower() if status is not None else ""
        if normalized_status in {"failed", "canceled"}:
            raise MeilisearchTaskFailedError(status, error)

    @staticmethod
    def _normalize_document_id(raw_id: Any) -> str:
        """
        Normalize a document identifier to a Meilisearch-safe string.

        Parameters:
            raw_id (Any): Original document identifier; will be converted to string.

        Returns:
            str: The input string if it matches the allowed ID pattern (1-511 characters: letters, digits, underscore, hyphen). Otherwise a deterministic fallback string prefixed with "gm_" derived from the input.
        """
        value = str(raw_id)
        if MeilisearchBackend._ID_PATTERN.match(value):
            return value
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        return f"gm_{digest}"


def _escape_filter_value(value: Any) -> str:
    """
    Escape a value for inclusion in a Meilisearch filter expression.

    Parameters:
        value (Any): Value to be escaped; it will be converted to a string.

    Returns:
        str: The input converted to a string with backslashes and double quotes escaped.
    """
    escaped = str(value)
    escaped = escaped.replace("\\", "\\\\").replace('"', '\\"')
    return escaped


class MeilisearchTaskFailedError(SearchBackendError):
    """Raised when a Meilisearch task fails to complete successfully."""

    def __init__(self, status: str | None, error: Any | None) -> None:
        """
        Initializes the MeilisearchTaskFailedError with the task status and error details.

        Parameters:
            status (str | None): Final status reported for the task (e.g., "failed", "canceled").
            error (Any | None): Error information returned by Meilisearch for the task.
        """
        super().__init__(
            f"Meilisearch task did not succeed (status={status}, error={error})."
        )
