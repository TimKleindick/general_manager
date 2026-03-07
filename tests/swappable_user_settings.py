from tests.test_settings import *  # noqa: F403

INSTALLED_APPS = [
    *INSTALLED_APPS,  # type: ignore[name-defined]  # noqa: F405
    "tests.custom_user_app.apps.CustomUserAppConfig",
]

AUTH_USER_MODEL = "custom_user_app.User"
AUTOCREATE_GRAPHQL = False
