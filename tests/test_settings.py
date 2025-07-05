SECRET_KEY = "test-secret-key"
DEBUG = True

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    # deine App-Package(s):
    "general_manager",  # falls du pip install -e . genutzt hast
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Alle weiteren von deinem Code abgefragten Settings
AUTOCREATE_GRAPHQL = True
SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"
ROOT_URLCONF = "tests.test_urls"

MIDDLEWARE = [
    # ggf. noch andere Middleware …
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
]
