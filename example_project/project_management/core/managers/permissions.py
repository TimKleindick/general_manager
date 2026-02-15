from __future__ import annotations

from general_manager.permission import register_permission

from .constants import (
    LEGACY_CREATE_ALLOWED_IDENTIFIERS,
    LEGACY_MANAGEMENT_IDENTIFIERS,
    PHASE_IDS_WITH_FIXED_NOMINATION_PROBABILITY,
)
from .ids import (
    _request_user_id,
    _resolve_customer_id,
    _resolve_project_id,
    _resolve_project_phase_type_id,
    _resolve_user_identifier,
    _to_int,
)


@register_permission("isProjectRoleAny")
def _permission_is_project_role_any(instance, user, config: list[str]) -> bool:
    from .project_domain import ProjectTeam

    user_id = _request_user_id(user)
    if user_id is None:
        return False
    project_id = _resolve_project_id(instance)
    if project_id is None:
        return False
    role_ids = {_to_int(raw_id) for raw_id in config}
    role_ids.discard(None)
    if not role_ids:
        return False
    for team_entry in ProjectTeam.filter(
        project_id=project_id,
        responsible_user_id=user_id,
        active=True,
    ):
        if _to_int(getattr(team_entry, "project_user_role_id", None)) in role_ids:
            return True
    return False


@register_permission("isKeyAccountOfProjectCustomer")
def _permission_is_key_account_of_project_customer(
    instance, user, _config: list[str]
) -> bool:
    from .master_data import Customer

    user_id = _request_user_id(user)
    if user_id is None:
        return False
    customer_id = _resolve_customer_id(instance)
    if customer_id is None:
        return False
    for customer in Customer.filter(id=customer_id):
        if _to_int(getattr(customer, "key_account_id", None)) == user_id:
            return True
    return False


@register_permission("isLegacyProjectCreateAllowed")
def _permission_is_legacy_project_create_allowed(
    _instance, user, _config: list[str]
) -> bool:
    from .identity import User
    from .master_data import Customer

    user_id = _request_user_id(user)
    if user_id is None:
        return False

    user_manager = User.filter(id=user_id).first()
    identifier = _resolve_user_identifier(user_manager)
    if identifier in LEGACY_CREATE_ALLOWED_IDENTIFIERS:
        return True
    if identifier in LEGACY_MANAGEMENT_IDENTIFIERS:
        return True

    for customer in Customer.all():
        if _to_int(getattr(customer, "key_account_id", None)) == user_id:
            return True
    return False


@register_permission("canUpdateProbabilityOfNomination")
def _permission_can_update_probability_of_nomination(
    instance, user, _config: list[str]
) -> bool:
    phase_type_id = _resolve_project_phase_type_id(instance)
    if phase_type_id in PHASE_IDS_WITH_FIXED_NOMINATION_PROBABILITY:
        return False
    return _permission_is_project_role_any(instance, user, ["1"]) or (
        _permission_is_key_account_of_project_customer(instance, user, [])
    )
