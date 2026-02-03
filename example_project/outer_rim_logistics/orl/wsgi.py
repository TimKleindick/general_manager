from __future__ import annotations

import os

from django.core.wsgi import get_wsgi_application
from orl.settings import ensure_log_dir_exists

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "orl.settings")

ensure_log_dir_exists()
application = get_wsgi_application()
