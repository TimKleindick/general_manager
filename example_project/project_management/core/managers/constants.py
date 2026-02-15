from __future__ import annotations

from random import SystemRandom

LEGACY_CREATE_ALLOWED_IDENTIFIERS: set[str] = {
    "seed_user_1",
    "pm_admin",
}

LEGACY_MANAGEMENT_IDENTIFIERS: set[str] = {
    "seed_user_1",
    "pm_admin",
}

PHASE_IDS_WITH_FIXED_NOMINATION_PROBABILITY: set[int] = {3, 4, 5, 6, 7, 8}
_RNG = SystemRandom()
