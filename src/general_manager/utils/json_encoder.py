"""JSON encoding helpers for GeneralManager cache and payload utilities."""

from datetime import date, datetime, time
import json

from general_manager.manager.general_manager import GeneralManager


class CustomJSONEncoder(json.JSONEncoder):
    """JSON encoder for values commonly found in GeneralManager payloads.

    Dates, datetimes, and times are emitted with `isoformat()`.
    `GeneralManager` instances are represented as
    `<ClassName>(**<identification>)`. Values supported by the standard JSON
    encoder keep the normal `json.JSONEncoder` behavior. Unsupported values fall
    back to `str(value)` instead of raising `TypeError`.
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
            manager_class_name = type.__getattribute__(o.__class__, "__name__")
            return f"{manager_class_name}(**{o.identification})"
        try:
            return super().default(o)
        except TypeError:
            # Fallback: convert all other objects to str
            return str(o)
