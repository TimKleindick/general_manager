from __future__ import annotations

import secrets
from pathlib import Path

from django.http import HttpRequest, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from core.image_utils import project_image_directory, resolve_project_image_url
from core.managers.project_domain import Project


def _dashboard_asset_version() -> str:
    assets_dir = Path(__file__).resolve().parent / "static/core/dashboard_app/assets"
    candidates = [assets_dir / "app.js", assets_dir / "app.css"]
    mtimes = [int(path.stat().st_mtime) for path in candidates if path.exists()]
    if not mtimes:
        return "dev"
    return str(max(mtimes))


@ensure_csrf_cookie
def project_list_view(request: HttpRequest):
    return render(
        request,
        "core/spa_entry.html",
        {"dashboard_asset_version": _dashboard_asset_version()},
    )


@ensure_csrf_cookie
def dashboard_view(request: HttpRequest):
    return render(
        request,
        "core/spa_entry.html",
        {"dashboard_asset_version": _dashboard_asset_version()},
    )


@require_POST
def upload_project_image_view(request: HttpRequest, project_id: int) -> JsonResponse:
    if not request.user.is_authenticated:
        return JsonResponse(
            {"ok": False, "error": "Authentication required."}, status=403
        )

    upload = request.FILES.get("image")
    if upload is None:
        return JsonResponse({"ok": False, "error": "Missing image file."}, status=400)

    allowed_extensions = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
    extension = Path(upload.name or "").suffix.lower()
    if extension not in allowed_extensions:
        return JsonResponse(
            {"ok": False, "error": "Unsupported image type."}, status=400
        )
    if upload.size > 10 * 1024 * 1024:
        return JsonResponse(
            {"ok": False, "error": "Image is too large (max 10 MB)."}, status=400
        )

    project = Project.filter(id=project_id).first()
    if project is None:
        return JsonResponse({"ok": False, "error": "Project not found."}, status=404)

    image_group_id = project.project_image_group_id or secrets.randbelow(10**9) + 1
    image_dir = project_image_directory(image_group_id)
    image_dir.mkdir(parents=True, exist_ok=True)
    for existing in image_dir.glob("*"):
        if existing.is_file():
            existing.unlink()

    target_path = image_dir / f"project-{project_id}{extension}"
    with target_path.open("wb+") as output:
        for chunk in upload.chunks():
            output.write(chunk)

    try:
        project = project.update(
            creator_id=request.user.id,
            project_image_group_id=image_group_id,
        )
    except PermissionError:
        return JsonResponse(
            {"ok": False, "error": "You do not have permission to update this project image."},
            status=403,
        )
    return JsonResponse(
        {
            "ok": True,
            "projectId": project_id,
            "projectImageUrl": resolve_project_image_url(project.project_image_group_id),
        }
    )
