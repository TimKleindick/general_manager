from datetime import datetime, date, time
import json
from general_manager.manager.generalManager import GeneralManager


class CustomJSONEncoder(json.JSONEncoder):
    def default(self, o):

        # datetime-Objekte als ISO-Strings serialisieren
        if isinstance(o, (datetime, date, time)):
            return o.isoformat()
        # Fallback: alle anderen Objekte als str()
        if isinstance(o, GeneralManager):
            return f"{o.__class__.__name__}(**{o.identification})"
        try:
            return super().default(o)
        except TypeError:
            return str(o)
