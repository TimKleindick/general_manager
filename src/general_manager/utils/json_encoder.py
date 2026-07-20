"""JSON encoding helpers for GeneralManager cache and payload utilities."""

from datetime import date, datetime, time
import json

from general_manager.as_of import search_date_cache_fingerprint
from general_manager.manager.general_manager import GeneralManager


class CustomJSONEncoder(json.JSONEncoder):
    """JSON encoder for values commonly found in GeneralManager payloads.

    Dates, datetimes, and times are emitted with `isoformat()`.
    Current `GeneralManager` instances are represented as
    `<ClassName>(**<identification>)`; snapshot-bound instances append an
    `@as_of(<isoformat>)` suffix. Values supported by the standard JSON encoder
    keep the normal `json.JSONEncoder` behavior. Unsupported values fall back to
    `str(value)` instead of raising `TypeError`.
    """

    def default(self, o: object) -> object:
        """Return a JSON-compatible value for `o`.

        Raises:
            Exception: Errors raised by `isoformat()`,
                `GeneralManager.identification`, or `str(o)` are allowed to
                propagate.
        """
        if isinstance(o, (datetime, date, time)):
            return o.isoformat()
        if isinstance(o, GeneralManager):
            # Bypass GeneralManagerMeta.__getattribute__ descriptor initialization.
            manager_class_name = type.__getattribute__(o.__class__, "__name__")
            manager_value = f"{manager_class_name}(**{o.identification})"
            search_date = o.__dict__.get("_effective_search_date")
            if isinstance(search_date, datetime):
                manager_value += f"@as_of({search_date_cache_fingerprint(search_date)})"
            return manager_value
        try:
            return super().default(o)
        except TypeError:
            # Fallback: convert all other objects to str
            return str(o)
