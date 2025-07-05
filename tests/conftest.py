# tests/conftest.py

from django.apps import apps as global_apps

# 1) Original‐Funktion sichern
_original_get_app = global_apps.get_containing_app_config


# 2) Patch schreiben
def _pytest_get_containing_app_config(object_name: str):
    # erst die normale Suche probieren
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
