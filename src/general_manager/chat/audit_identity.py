"""Opaque planned audit identities shared by runtime and offline replay."""

import hashlib


def planned_audit_lineage_id(value: object) -> str | None:
    """Return a deterministic opaque audit identifier for planner-controlled IDs."""
    if not isinstance(value, str) or not value or len(value) > 128:
        return None
    return hashlib.sha256(value.encode()).hexdigest()
