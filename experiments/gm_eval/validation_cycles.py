"""Source-bound state subpredicate for offline validation-cycle certificates.

This does not establish a complete certificate or permission to retry. Callers
must retain their independent HTTP/lifecycle, source, history, action/feedback,
pass-order, audit, fixture, auth and cleanup gates, including global STOPs.
"""

from collections.abc import Mapping
from hashlib import sha256
from typing import Any

from general_manager.chat.planned.scheduler import execution_state_signature


def validation_cycle_state_matches(
    first: Mapping[str, Any], repeated: Mapping[str, Any], audit_sha256: object
) -> bool:
    """Require equal material states and their exact runtime audit digest."""
    first_signature = execution_state_signature(first)
    return (
        first_signature == execution_state_signature(repeated)
        and isinstance(audit_sha256, str)
        and sha256(first_signature.encode()).hexdigest() == audit_sha256
    )
