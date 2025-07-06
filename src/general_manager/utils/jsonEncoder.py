from datetime import datetime, date, time
import json
from general_manager.manager.generalManager import GeneralManager


class CustomJSONEncoder(json.JSONEncoder):
    def default(self, o):

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
