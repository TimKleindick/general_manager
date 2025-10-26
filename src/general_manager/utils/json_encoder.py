"""Custom JSON encoder capable of rendering GeneralManager objects."""

from datetime import datetime, date, time
import json
from general_manager.manager.general_manager import GeneralManager


class CustomJSONEncoder(json.JSONEncoder):
    """Serialise complex objects that appear within GeneralManager payloads."""

    def default(self, o: object) -> object:
        """
        Convert unsupported objects into JSON-friendly representations.

        Parameters:
            o (Any): Object to encode.

        Returns:
            Any: JSON-serialisable representation of the object.
        """

        # Serialize datetime objects as ISO strings
        if isinstance(o, (datetime, date, time)):
            return o.isoformat()
        # Handle GeneralManager instances
        if isinstance(o, GeneralManager):
            return f"{o.__class__.__name__}(**{o.identification})"
        try:
            return super().default(o)
        except TypeError:
            # Fallback: convert all other objects to str
            return str(o)
