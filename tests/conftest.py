# tests/conftest.py

from django.apps import apps as global_apps

# 1) Original‐Funktion sichern
_original_get_app = global_apps.get_containing_app_config


# 2) Patch schreiben
def _pytest_get_containing_app_config(object_name: str):
    # erst die normale Suche probieren
    """
    Return the app config containing the given object name, or fall back to the "general_manager" app config if not found.
    
    If the original lookup does not find a matching app config, attempts to return the config for the "general_manager" app. Returns None if neither is found.
    
    Parameters:
        object_name (str): The dotted path of the object to locate.
    
    Returns:
        AppConfig or None: The app config containing the object, the "general_manager" app config, or None if neither is found.
    """
    cfg = _original_get_app(object_name)
    if cfg is not None:
        return cfg
    # Wenn das fehlgeschlagen ist, nehmen wir einfach general_manager
    try:
        return global_apps.get_app_config("general_manager")
    except LookupError:
        return None


# 3) Patch aufschalten
global_apps.get_containing_app_config = _pytest_get_containing_app_config
