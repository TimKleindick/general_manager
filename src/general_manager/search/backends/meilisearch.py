"""Meilisearch backend adapter."""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable, Iterable
from importlib import import_module
from typing import Mapping, Protocol, Sequence, cast

from general_manager.search.backend import (
    SearchBackendClientMissingError,
    SearchBackendError,
    SearchDocument,
    SearchHit,
    SearchResult,
)


def _load_meilisearch_api_error() -> type[Exception] | None:
    """Return the optional Meilisearch API error class when installed."""
    try:
        errors_module = import_module("meilisearch.errors")
    except ImportError:  # pragma: no cover - only needed when backend is unused
        return None
    error_type = getattr(errors_module, "MeilisearchApiError", None)
    return error_type if isinstance(error_type, type) else None


MeilisearchApiError = _load_meilisearch_api_error()


class _MeilisearchIndex(Protocol):
    """Subset of Meilisearch index APIs used by this adapter."""

    def update_settings(self, payload: Mapping[str, object]) -> object: ...

    def add_documents(self, payload: Sequence[Mapping[str, object]]) -> object: ...

    def delete_documents(self, ids: Sequence[str]) -> object: ...

    def search(self, query: str, payload: Mapping[str, object]) -> object: ...

    def get_documents(self, payload: Mapping[str, object]) -> object: ...


class _MeilisearchClient(Protocol):
    """Subset of Meilisearch client APIs used by this adapter."""

    def get_index(self, name: str) -> _MeilisearchIndex: ...

    def create_index(self, name: str, options: Mapping[str, object]) -> object: ...


class _MeilisearchModule(Protocol):
    """Runtime Meilisearch package shape used by the adapter."""

    Client: Callable[[str, str | None], _MeilisearchClient]


class _MeilisearchClientWithGetOrCreate(_MeilisearchClient, Protocol):
    """Client variant that can create or fetch an index in one call."""

    def get_or_create_index(
        self,
        name: str,
        options: Mapping[str, object],
    ) -> _MeilisearchIndex: ...


class _MeilisearchClientWithWait(_MeilisearchClient, Protocol):
    """Client variant exposing server-side task waiting."""

    def wait_for_task(self, task_uid: object) -> object: ...


class _MeilisearchClientWithGetTask(_MeilisearchClient, Protocol):
    """Client variant exposing polling-based task lookup."""

    def get_task(self, task_uid: object) -> object: ...


class MeilisearchBackend:
    """
    Meilisearch implementation of the SearchBackend protocol.

    The adapter stores the original GeneralManager document id in
    ``gm_document_id`` and uses a Meilisearch-safe deterministic ``id`` for the
    primary key. Already-safe ids are kept unchanged when they match
    ``^[A-Za-z0-9_-]{1,511}$``. All other ids, including empty strings and
    Unicode strings, are converted to ``"gm_" + sha256(str(id)).hexdigest()``;
    the mapping is stable and collision-resistant but not reversible. It accepts
    a preconfigured client for tests or advanced deployments, otherwise imports
    ``meilisearch.Client`` at runtime.
    """

    _ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,511}$")

    def __init__(
        self,
        url: str = "http://127.0.0.1:7700",
        api_key: str | None = None,
        client: _MeilisearchClient | None = None,
    ) -> None:
        """
        Initialize the backend with a provided Meilisearch client or a new one.

        Parameters:
            url: Base URL to use when creating a Meilisearch client if
                `client` is not provided.
            api_key: Optional API key to use when creating the client.
            client: Optional preconfigured Meilisearch client instance to use
                directly. If omitted, the constructor imports the
                `meilisearch` package and instantiates a client with `url` and
                `api_key`; if the package is not available, raises
                SearchBackendClientMissingError("Meilisearch").

        Raises:
            SearchBackendClientMissingError: The `meilisearch` package is not
                installed and no client was provided.
        """
        if client is None:
            try:
                meilisearch_module = import_module("meilisearch")
            except ImportError as exc:
                raise SearchBackendClientMissingError("Meilisearch") from exc
            client_factory = cast(_MeilisearchModule, meilisearch_module).Client
            client = client_factory(url, api_key)
        self._client = client

    def ensure_index(self, index_name: str, settings: Mapping[str, object]) -> None:
        """
        Ensure a Meilisearch index with the given name exists and apply searchable, filterable, and sortable field settings.

        Parameters:
            index_name (str): Name of the index to retrieve or create.
            settings: Index settings mapping. Recognized keys:
                - "searchable_fields": iterable of field names to set as searchableAttributes.
                - "filterable_fields": iterable of field names to set as filterableAttributes.
                - "sortable_fields": iterable of field names to set as sortableAttributes.

        Non-iterable setting values and strings/bytes are ignored; iterable
        values such as lists, tuples, and sets are converted to strings. This
        method creates the index with primary key ``id`` if it does not exist
        and waits for each settings update task to complete before returning.

        Raises:
            MeilisearchTaskFailedError: A create-index or settings task fails,
                is canceled, or times out while polling a client without
                `wait_for_task`.
        """
        index = self._get_or_create_index(index_name)
        searchable_fields = self._string_sequence_setting(settings, "searchable_fields")
        filterable_fields = self._string_sequence_setting(settings, "filterable_fields")
        sortable_fields = self._string_sequence_setting(settings, "sortable_fields")
        if searchable_fields is not None:
            task = index.update_settings(
                {"searchableAttributes": list(searchable_fields)}
            )
            self._wait_for_task(task)
        if filterable_fields is not None:
            task = index.update_settings(
                {"filterableAttributes": list(filterable_fields)}
            )
            self._wait_for_task(task)
        if sortable_fields is not None:
            task = index.update_settings({"sortableAttributes": list(sortable_fields)})
            self._wait_for_task(task)

    def upsert(self, index_name: str, documents: Sequence[SearchDocument]) -> None:
        """
        Ensure the index exists, then index or update the given documents and wait for the indexing task to complete.

        The index is still ensured when ``documents`` is empty, but no
        ``add_documents`` task is submitted.

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

        An empty ``ids`` sequence is a no-op and does not access Meilisearch.
        Non-empty sequences are normalized element-by-element; an empty string
        inside the sequence is treated as a real document id and deleted using
        its deterministic normalized value.

        Parameters:
                index_name (str): Name of the index to delete documents from.
                ids (Sequence[str]): Sequence of document IDs to remove; each ID will be normalized before deletion. If empty, no action is taken.

        Raises:
                MeilisearchTaskFailedError: If the backend reports the deletion task failed or was canceled.
        """
        if not ids:
            return
        index = self._get_or_create_index(index_name)
        normalized_ids = [self._normalize_document_id(doc_id) for doc_id in ids]
        task = index.delete_documents(normalized_ids)
        self._wait_for_task(task)

    def list_document_ids(
        self,
        index_name: str,
        *,
        types: Sequence[str] | None = None,
    ) -> set[str]:
        """
        Return stored GeneralManager document IDs from a Meilisearch index.

        Reads pages of up to 1000 documents using fields ``id``,
        ``gm_document_id``, and ``type``. ``types=None`` and ``types=[]`` both
        include every type. When a filter is present, matching is exact against
        the stored ``type`` value. ``gm_document_id`` is preferred; legacy
        documents without it fall back to ``id``. The fallback also applies when
        ``gm_document_id`` is falsey, such as an empty string. Duplicate ids
        collapse into one set entry, and malformed document entries without
        either field are ignored.
        """
        index = self._get_or_create_index(index_name)
        document_ids: set[str] = set()
        type_filter = set(types or ())
        limit = 1000
        offset = 0

        while True:
            response = index.get_documents(
                {
                    "limit": limit,
                    "offset": offset,
                    "fields": ["id", "gm_document_id", "type"],
                }
            )
            documents = self._extract_documents_results(response)
            for document in documents:
                document_type = self._read_document_field(document, "type")
                if type_filter and document_type not in type_filter:
                    continue
                document_id = self._read_document_field(
                    document, "gm_document_id"
                ) or self._read_document_field(document, "id")
                if document_id is not None:
                    document_ids.add(str(document_id))

            count = len(documents)
            if count == 0:
                break
            offset += count
            total = self._extract_documents_total(response)
            if count < limit or (total is not None and offset >= total):
                break

        return document_ids

    def search(
        self,
        index_name: str,
        query: str,
        *,
        filters: Mapping[str, object] | Sequence[Mapping[str, object]] | None = None,
        filter_expression: str | None = None,
        sort_by: str | None = None,
        sort_desc: bool = False,
        limit: int = 10,
        offset: int = 0,
        types: Sequence[str] | None = None,
    ) -> SearchResult:
        """
        Execute a search against the specified Meilisearch index.

        Parameters:
            index_name (str): Name of the index to search.
            query (str): Full-text query string.
            filters: Field-based filter(s). May be a single mapping or a
                sequence of mappings representing OR groups; keys may include
                nested lookups (e.g., "field__lookup").
            filter_expression (str | None): Raw Meilisearch filter expression to use instead of `filters`.
            sort_by (str | None): Field name to sort results by.
            sort_desc (bool): If true, sort in descending order; otherwise ascending.
            limit (int): Maximum number of results to return.
            offset (int): Number of results to skip.
            types (Sequence[str] | None): Sequence of document type names to restrict results to the `type` field.

        Returns:
            SearchResult: Object containing matched hits, total hits estimate, request processing time in milliseconds, and the raw Meilisearch response.

        Notes:
            `filter_expression` takes precedence over structured `filters` and
            `types`; when it is provided, ``types`` is ignored. Structured
            filters support equality and `in` semantics. Multiple fields inside
            one mapping are combined with AND, multiple filter mappings are
            combined with OR, and ``types`` are ORed together before being ANDed
            with structured filters. String rendering uses ``str(value)`` and
            escapes backslashes and double quotes; this also applies to numbers,
            booleans, and ``None``. Empty `in` lists produce an empty
            parenthesized clause. ``sort_by`` is treated as one raw field name
            and the adapter appends ``:asc`` or ``:desc``; it does not validate,
            split, or escape the sort field. Malformed hit entries are skipped.
            Missing hit ``id``/``gm_document_id`` and ``type`` become empty
            strings, missing ``identification`` and ``data`` become empty
            mappings, and missing ``_rankingScore`` becomes ``None``.
        """
        index = self._get_or_create_index(index_name)
        payload: dict[str, object] = {
            "limit": limit,
            "offset": offset,
        }
        filter_expr = (
            filter_expression
            if filter_expression is not None
            else self._build_filter_expression(filters, types)
        )
        if filter_expr:
            payload["filter"] = filter_expr
        if sort_by:
            direction = "desc" if sort_desc else "asc"
            payload["sort"] = [f"{sort_by}:{direction}"]

        response = self._as_mapping(index.search(query, payload))
        hits = [
            SearchHit(
                id=self._string_field(hit, "gm_document_id")
                or self._string_field(hit, "id")
                or "",
                type=self._string_field(hit, "type") or "",
                identification=self._dict_field(hit, "identification"),
                score=self._float_field(hit, "_rankingScore"),
                index=index_name,
                data=self._mapping_field(hit, "data"),
            )
            for hit in self._search_hits(response)
        ]
        return SearchResult(
            hits=hits,
            total=self._int_field(response, "estimatedTotalHits") or len(hits),
            took_ms=self._int_field(response, "processingTimeMs"),
            raw=response,
        )

    def _get_or_create_index(self, index_name: str) -> _MeilisearchIndex:
        """
        Ensure a Meilisearch index with the given name exists and return it.

        If the index does not exist, create it with primary key "id" and wait for the creation task to complete.

        Returns:
            The Meilisearch index object for the given index name.
        """
        if hasattr(self._client, "get_or_create_index"):
            return cast(
                _MeilisearchClientWithGetOrCreate, self._client
            ).get_or_create_index(index_name, {"primaryKey": "id"})
        if MeilisearchApiError is None:  # pragma: no cover - defensive fallback
            return self._client.get_index(index_name)
        api_error = MeilisearchApiError
        try:
            return self._client.get_index(index_name)
        except api_error as exc:
            if not _is_meilisearch_not_found(exc):
                raise
        try:
            task = self._client.create_index(index_name, {"primaryKey": "id"})
        except api_error as exc:
            if not _is_meilisearch_already_exists(exc):
                raise
            return self._client.get_index(index_name)
        self._wait_for_task(task)
        return self._client.get_index(index_name)

    @staticmethod
    def _document_payload(document: SearchDocument) -> dict[str, object]:
        """
        Build a Meilisearch-ready document payload from a SearchDocument.

        Parameters:
            document (SearchDocument): The source document whose fields and data will be mapped into the payload.

        Returns:
            dict[str, object]: A dictionary containing:
                - `id`: normalized document id suitable for Meilisearch,
                - `gm_document_id`: the original document id,
                - `type`: the document type,
                - `identification`: the document identification value,
                - `data`: the original data mapping,
                - additional top-level keys copied from `document.data` except
                  reserved keys (`id`, `gm_document_id`, `type`,
                  `identification`, `data`).
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
        filters: Mapping[str, object] | Sequence[Mapping[str, object]] | None,
        types: Sequence[str] | None,
    ) -> str | None:
        """
        Builds a Meilisearch-compatible filter expression from the given filters and types.

        Parameters:
            filters:
                A single filter mapping or a sequence of filter mappings.
                Each mapping's keys are field names or lookups using `field__lookup`.
                - Keys without `__` use exact equality.
                - `__in` or a list/tuple/set value creates an OR group for that field.
                The exact emitted comparison form is
                ``field = "escaped_value"`` for every value type. ``None``
                renders as ``"None"``, ``True`` as ``"True"``, ``False`` as
                ``"False"``, and numbers as their decimal string. Multiple
                fields in one mapping are combined with AND; multiple mappings
                are combined with OR. Empty `in` lists produce the exact clause
                ``()``.
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

    def _wait_for_task(self, task: object) -> None:
        """
        Waits for a Meilisearch task to complete using the backend client.

        Parameters:
            task: A task object or task-like response from which a task UID will be extracted.

        Raises:
            MeilisearchTaskFailedError: If the task finished with status
                "failed" or "canceled", or if fallback polling via
                ``get_task()`` does not reach "succeeded", "failed", or
                "canceled" within five seconds. Mapping responses read
                ``status`` and ``error`` keys; object responses read attributes
                with the same names. Unknown and missing statuses are treated as
                non-terminal while polling. Missing task UIDs are treated as
                already complete.
        """
        task_uid = self._extract_task_uid(task)
        if task_uid is None:
            return
        if hasattr(self._client, "wait_for_task"):
            result = cast(_MeilisearchClientWithWait, self._client).wait_for_task(
                task_uid
            )
            self._raise_for_failed_task(result)
            return
        if hasattr(self._client, "get_task"):
            polling_client = cast(_MeilisearchClientWithGetTask, self._client)
            timeout_seconds = 5.0
            poll_interval = 0.1
            start = time.monotonic()
            last_result: object | None = None
            while True:
                result = polling_client.get_task(task_uid)
                last_result = result
                status = self._extract_task_status(result)
                if status in {"succeeded", "failed", "canceled"}:
                    self._raise_for_failed_task(result)
                    return
                if time.monotonic() - start >= timeout_seconds:
                    raise MeilisearchTaskFailedError(
                        "timeout",
                        {"task_uid": task_uid, "status": status, "result": last_result},
                    )
                time.sleep(poll_interval)
                poll_interval = min(poll_interval * 1.5, 1.0)

    @staticmethod
    def _extract_task_uid(task: object) -> object | None:
        """
        Extract the Meilisearch task UID from a mapping or object.

        Checks common key names ("taskUid", "task_uid", "uid", "taskId") when `task` is a mapping,
        or the corresponding attribute names ("task_uid", "taskUid", "uid", "task_id") when `task`
        is an object, and returns the first matching value found.

        Parameters:
            task: A task mapping or object from which to extract the UID.

        Returns:
            object | None: The extracted task UID if present, otherwise `None`.
        """
        if task is None:
            return None
        if isinstance(task, Mapping):
            value = (
                task.get("taskUid")
                or task.get("task_uid")
                or task.get("uid")
                or task.get("taskId")
            )
            return value
        for name in ("task_uid", "taskUid", "uid", "task_id"):
            attr_value = cast(object | None, getattr(task, name, None))
            if attr_value is not None:
                return attr_value
        return None

    @staticmethod
    def _extract_task_status(result: object) -> str | None:
        """Read a normalized task status from a Meilisearch task response."""
        if result is None:
            return None
        if isinstance(result, Mapping):
            status = result.get("status")
        else:
            status = getattr(result, "status", None)
        return str(status).lower() if status is not None else None

    @staticmethod
    def _raise_for_failed_task(result: object) -> None:
        """
        Raise MeilisearchTaskFailedError when a Meilisearch task result indicates failure or cancellation.

        Parameters:
            result: A task result object or mapping. Accepted shapes:
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
    def _extract_documents_results(response: object) -> list[object]:
        """Read document result entries from a Meilisearch documents response."""
        if response is None:
            return []
        if isinstance(response, Mapping):
            results = response.get("results")
        else:
            results = getattr(response, "results", None)
        if results is None:
            return []
        return list(results)

    @staticmethod
    def _extract_documents_total(response: object) -> int | None:
        """Read the total document count from a Meilisearch documents response."""
        if response is None:
            return None
        if isinstance(response, Mapping):
            total = response.get("total")
        else:
            total = getattr(response, "total", None)
        return total if isinstance(total, int) else None

    @staticmethod
    def _read_document_field(document: object, field: str) -> object | None:
        """Read a field from a mapping or object document payload."""
        if isinstance(document, Mapping):
            return document.get(field)
        return getattr(document, field, None)

    @staticmethod
    def _string_sequence_setting(
        settings: Mapping[str, object],
        key: str,
    ) -> list[str] | None:
        """Return a string list from a backend setting value."""
        value = settings.get(key)
        if value is None:
            return None
        if not isinstance(value, Iterable) or isinstance(value, (str, bytes)):
            return None
        return [str(item) for item in value]

    @staticmethod
    def _as_mapping(value: object) -> Mapping[str, object]:
        """Return a mapping response or an empty mapping for unsupported shapes."""
        return value if isinstance(value, Mapping) else {}

    @staticmethod
    def _search_hits(response: Mapping[str, object]) -> list[Mapping[str, object]]:
        """Return mapping-shaped hits from a Meilisearch search response."""
        hits = response.get("hits")
        if not isinstance(hits, Sequence) or isinstance(hits, (str, bytes)):
            return []
        return [hit for hit in hits if isinstance(hit, Mapping)]

    @staticmethod
    def _string_field(document: Mapping[str, object], field: str) -> str | None:
        """Read a field as text when present."""
        value = document.get(field)
        return str(value) if value is not None else None

    @staticmethod
    def _dict_field(document: Mapping[str, object], field: str) -> dict[str, object]:
        """Read a mapping field as a plain dictionary."""
        value = document.get(field)
        return dict(value) if isinstance(value, Mapping) else {}

    @staticmethod
    def _mapping_field(
        document: Mapping[str, object], field: str
    ) -> Mapping[str, object]:
        """Read a mapping field while preserving read-only mapping semantics."""
        value = document.get(field)
        return value if isinstance(value, Mapping) else {}

    @staticmethod
    def _float_field(document: Mapping[str, object], field: str) -> float | None:
        """Read a numeric field as a float."""
        value = document.get(field)
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        return None

    @staticmethod
    def _int_field(document: Mapping[str, object], field: str) -> int | None:
        """Read a numeric field as an integer."""
        value = document.get(field)
        if isinstance(value, bool):
            return None
        return value if isinstance(value, int) else None

    @staticmethod
    def _normalize_document_id(raw_id: object) -> str:
        """
        Normalize a document identifier to a Meilisearch-safe string.

        Parameters:
            raw_id: Original document identifier; will be converted to string.

        Returns:
            str: The input string if it matches the allowed ID pattern (1-511 characters: letters, digits, underscore, hyphen). Otherwise a deterministic fallback string prefixed with "gm_" derived from the input.
        """
        value = str(raw_id)
        if MeilisearchBackend._ID_PATTERN.match(value):
            return value
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        return f"gm_{digest}"


def _meilisearch_error_code(error: Exception) -> str:
    """Extract a normalized Meilisearch error code from known error shapes."""
    for attr in ("error_code", "code", "errorCode"):
        value = getattr(error, attr, None)
        if value:
            return str(value).lower()
    return ""


def _meilisearch_status_code(error: Exception) -> int | None:
    """Extract an HTTP status code from known Meilisearch error shapes."""
    for attr in ("status_code", "statusCode", "http_status", "status"):
        value = getattr(error, attr, None)
        if isinstance(value, int):
            return value
    return None


def _is_meilisearch_not_found(error: Exception) -> bool:
    """Return whether a Meilisearch error represents a missing resource."""
    code = _meilisearch_error_code(error)
    status = _meilisearch_status_code(error)
    return status == 404 or "not_found" in code


def _is_meilisearch_already_exists(error: Exception) -> bool:
    """Return whether a Meilisearch error represents an existing resource."""
    code = _meilisearch_error_code(error)
    status = _meilisearch_status_code(error)
    return status == 409 or "already_exists" in code


def _escape_filter_value(value: object) -> str:
    """
    Escape a value for inclusion in a Meilisearch filter expression.

    Parameters:
        value: Value to be escaped; it will be converted to a string.

    Returns:
        str: The input converted to a string with backslashes and double quotes escaped.
    """
    escaped = str(value)
    escaped = escaped.replace("\\", "\\\\").replace('"', '\\"')
    return escaped


class MeilisearchTaskFailedError(SearchBackendError):
    """Raised when a Meilisearch task fails to complete successfully."""

    def __init__(self, status: object | None, error: object | None) -> None:
        """
        Initializes the MeilisearchTaskFailedError with the task status and error details.

        Parameters:
            status: Final status reported for the task (e.g., "failed", "canceled").
            error: Error information returned by Meilisearch for the task.
        """
        super().__init__(
            f"Meilisearch task did not succeed (status={status}, error={error})."
        )
