SECRET_KEY = "test-secret-key"
DEBUG = True

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
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
AUTOCREATE_GRAPHQL = False
