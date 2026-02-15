from __future__ import annotations


def _to_int(value: object | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _extract_related_id(value: object | None) -> int | None:
    direct = _to_int(value)
    if direct is not None:
        return direct
    return _to_int(getattr(value, "id", None))


def _extract_identification_id(value: object | None) -> int | None:
    identification = getattr(value, "identification", None)
    if not isinstance(identification, dict):
        return None
    for key in ("id", "pk"):
        direct = _to_int(identification.get(key))
        if direct is not None:
            return direct
    for key, item in identification.items():
        if key.endswith("_id"):
            direct = _to_int(item)
            if direct is not None:
                return direct
    for item in identification.values():
        direct = _to_int(item)
        if direct is not None:
            return direct
    return None


def _request_user_id(user: object) -> int | None:
    return _to_int(getattr(user, "id", None))


def _resolve_project_id(instance: object) -> int | None:
    return (
        _extract_related_id(getattr(instance, "id", None))
        or _extract_identification_id(instance)
        or _extract_related_id(getattr(instance, "project_id", None))
        or _extract_related_id(getattr(instance, "project", None))
        or _extract_related_id(getattr(instance, "group_id", None))
    )


def _resolve_customer_id(instance: object) -> int | None:
    return (
        _extract_related_id(getattr(instance, "customer_id", None))
        or _extract_related_id(getattr(instance, "customer", None))
        or _extract_identification_id(getattr(instance, "customer", None))
    )


def _resolve_project_phase_type_id(instance: object) -> int | None:
    return _extract_related_id(
        getattr(instance, "project_phase_type_id", None)
    ) or _extract_related_id(getattr(instance, "project_phase_type", None))


def _resolve_user_identifier(instance: object) -> str | None:
    for field_name in ("microsoft_id", "username", "email"):
        value = getattr(instance, field_name, None)
        if isinstance(value, str) and value:
            return value
    return None
