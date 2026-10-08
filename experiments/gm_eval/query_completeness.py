"""Require complete query windows before using their rows as identity evidence."""

from collections.abc import Mapping
import json
from typing import Any

from .evidence_rows import _selections


def _collection_complete(
    value: Mapping[str, Any], fields: list[Any], kind: str
) -> bool:
    """Only uniquely selected count/flags may certify the returned collection."""
    selections = _selections(fields, kind)
    rows = value.get(kind)
    if (
        len(selections) != 1
        or not isinstance(selections[0], list)
        or not isinstance(rows, list)
    ):
        return False
    metadata = []
    if kind == "edges":
        # Legacy Connections may put totalCount on the Connection itself.
        metadata.append((value, fields))
    info_fields = [
        (name, _selections(fields, name))
        for name in ("pageInfo", "page_info")
        if _selections(fields, name)
    ]
    for name, chosen in info_fields:
        info = value.get(name)
        if (
            len(chosen) != 1
            or not isinstance(chosen[0], list)
            or not isinstance(info, Mapping)
        ):
            return False
        metadata.append((info, chosen[0]))
    counts = []
    flags: set[str] = set()
    for info, selected in metadata:
        for name in (
            "totalCount",
            "total_count",
            "hasNextPage",
            "has_next_page",
            "hasPreviousPage",
            "has_previous_page",
        ):
            chosen = _selections(selected, name)
            if not chosen:
                continue
            if chosen != [None]:
                return False
            actual = info.get(name)
            if name in {"totalCount", "total_count"}:
                if type(actual) is not int or actual < 0 or actual != len(rows):
                    return False
                counts.append(actual)
            else:
                if actual is not False:
                    return False
                flags.add(
                    "next" if name in {"hasNextPage", "has_next_page"} else "previous"
                )
    return bool(counts) or (kind == "edges" and flags == {"next", "previous"})


def _nested_complete(
    value: Any,
    fields: list[Any],
    manager_rows: set[tuple[str | int, ...]],
    coordinate: tuple[str | int, ...],
) -> bool:
    """Bind Page/Connection completeness to actual selected object fields."""
    if isinstance(value, list):
        return all(
            _nested_complete(item, fields, manager_rows, (*coordinate, index))
            for index, item in enumerate(value)
        )
    if value is None:
        return True
    if not isinstance(value, Mapping):
        return False
    # Typed manager objects may have ordinary relations named items/edges.
    # Their selected child wrappers still undergo the same recursive checks.
    for kind in ("items", "edges"):
        # GraphQL scalar/enum leaves, including lists and custom JSON values,
        # have no object selection and cannot denote a pagination wrapper.
        chosen = _selections(fields, kind)
        if any(isinstance(children, list) for children in chosen):
            if coordinate in manager_rows:
                # A duplicate/mixed object selection must not suppress child
                # recursion, even at a proven manager object.
                if len(chosen) != 1:
                    return False
            elif not _collection_complete(value, fields, kind):
                return False
    for name, item in value.items():
        chosen = _selections(fields, name)
        if len(chosen) == 1 and isinstance(chosen[0], list):
            if not _nested_complete(item, chosen[0], manager_rows, (*coordinate, name)):
                return False
    return True


def complete_identity_queries(
    calls: Mapping[str, Mapping[str, Any]],
    manager_rows: Mapping[str, set[tuple[str | int, ...]]] | None = None,
) -> set[str]:
    """Keep complete roots or exact, contiguous native/offset query windows.

    Only already linked successful calls enter this set. Identical repeated
    windows are harmless; conflicting repeats, gaps and duplicate row IDs are
    not. Exact arguments preserve manager, filter, order and selected projection.
    """
    complete: set[str] = set()
    groups: dict[str, list[tuple[str, int, Mapping[str, Any]]]] = {}
    for identifier, call in calls.items():
        args = call["arguments"]
        output = call.get("output", call.get("result"))
        roles = (manager_rows or {}).get(identifier, set())
        if (
            not isinstance(output, Mapping)
            or output.get("status") == "error"
            or output.get("errors")
            or ("complete" in output and type(output["complete"]) is not bool)
            or not _nested_complete(output["data"], args["fields"], roles, ("data",))
        ):
            continue
        rows, total = output["data"], output.get("total_count")
        native = args.get("arguments", {})
        offset = args.get("offset", 0)
        if (
            not isinstance(native, Mapping)
            or type(offset) is not int
            or offset < 0
            or type(total) is not int
            or total < len(rows)
        ):
            continue
        page = native.get("page", 1)
        if (
            offset == 0
            and type(page) is int
            and page == 1
            and total == len(rows)
            and output.get("has_more") is False
            and output.get("complete") is not False
        ):
            complete.add(identifier)
            continue
        if output.get("complete") is True:
            continue
        signature = {**args, "arguments": dict(native)}
        ordering = native.get("orderBy")
        if isinstance(ordering, list) and all(
            isinstance(term, Mapping) for term in ordering
        ):
            signature["arguments"]["orderBy"] = [
                {**term, "direction": term.get("direction", "ASC")} for term in ordering
            ]
        if "page" in native or "pageSize" in native:
            size = native.get("pageSize")
            if (
                offset != 0
                or type(page) is not int
                or page < 1
                or type(size) is not int
                or size < 1
                or total <= size
                or len(rows) != min(size, max(0, total - (page - 1) * size))
            ):
                continue
            offset = (page - 1) * size
            signature["arguments"].pop("page", None)
            mode = "native"
        else:
            limit = args.get("limit")
            if type(limit) is not int or limit < 1 or len(rows) > limit:
                continue
            signature.pop("offset", None)
            mode = "offset"
        key = json.dumps(
            [mode, call.get("source_turn"), signature],
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        groups.setdefault(key, []).append((identifier, offset, output))
    for group in groups.values():
        windows: dict[int, Mapping[str, Any]] = {}
        valid = True
        for _, offset, output in group:
            if offset in windows and windows[offset] != output:
                valid = False
                break
            windows[offset] = output
        total = group[0][2]["total_count"]
        cursor = 0
        identities: set[str] = set()
        for offset, output in sorted(windows.items()):
            rows = output["data"]
            ids = [
                str(row["id"])
                if type(row.get("id")) is int
                or (isinstance(row.get("id"), str) and row["id"])
                else None
                for row in rows
            ]
            if (
                offset != cursor
                or not rows
                or output["total_count"] != total
                or output.get("has_more") is not (cursor + len(rows) < total)
                or any(value is None or value in identities for value in ids)
                or len(set(ids)) != len(ids)
            ):
                valid = False
                break
            identities.update(value for value in ids if value is not None)
            cursor += len(rows)
        if valid and len(windows) > 1 and cursor == total:
            complete.update(identifier for identifier, _, _ in group)
    return complete
