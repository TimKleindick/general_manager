from __future__ import annotations

from pathlib import Path
from typing import Optional
from urllib.parse import quote

from django.conf import settings


def project_image_directory(group_id: int | str) -> Path:
    return Path(settings.MEDIA_ROOT) / "project_images" / str(group_id)


def resolve_project_image_url(group_id: int | None) -> Optional[str]:
    if not group_id:
        return None
    image_dir = project_image_directory(group_id)
    if not image_dir.exists() or not image_dir.is_dir():
        return None
    files = [path for path in image_dir.iterdir() if path.is_file()]
    if not files:
        return None
    latest = max(files, key=lambda item: item.stat().st_mtime)
    base_url = settings.MEDIA_URL.rstrip("/")
    return f"{base_url}/project_images/{group_id}/{quote(latest.name)}"
