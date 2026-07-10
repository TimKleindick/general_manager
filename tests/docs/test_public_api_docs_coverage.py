from __future__ import annotations

import json
import re
from importlib import import_module
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "tests" / "snapshots" / "public_api_exports.json"
DOCS_ROOT = ROOT / "docs"
ADDITIONAL_PUBLIC_MODULES = (
    "general_manager.workflow",
    "general_manager.metrics",
    "general_manager.seeding",
)
DOC_PATHS = [
    ROOT / "README.md",
    *[
        path
        for path in DOCS_ROOT.rglob("*.md")
        if not path.relative_to(DOCS_ROOT).as_posix().startswith("superpowers/")
    ],
]
UPLOAD_DOC_PATHS = (
    DOCS_ROOT / "concepts" / "graphql" / "file_uploads.md",
    DOCS_ROOT / "howto" / "graphql_file_uploads.md",
    DOCS_ROOT / "howto" / "graphql_file_uploads_s3.md",
)
UPLOAD_SETTING_NAMES = {
    "ENABLED",
    "HTTP_UPLOAD_PATH",
    "STAGING_PREFIX",
    "INTENT_DATABASE",
    "MAX_BYTES",
    "MAX_PENDING_INTENTS_PER_USER",
    "MAX_PENDING_BYTES_PER_USER",
    "MAX_PENDING_INTENTS_GLOBAL",
    "MAX_PENDING_BYTES_GLOBAL",
    "BEGIN_RATE_LIMIT_WINDOW_SECONDS",
    "MAX_BEGIN_ATTEMPTS_PER_USER",
    "MAX_BEGIN_ATTEMPTS_GLOBAL",
    "TRANSFER_LEASE_SECONDS",
    "TRANSFER_CREDENTIAL_TTL_SECONDS",
    "TRANSFER_RATE_LIMIT_WINDOW_SECONDS",
    "MAX_TRANSFER_ATTEMPTS_PER_USER",
    "MAX_TRANSFER_ATTEMPTS_GLOBAL",
    "MAX_TRANSFER_ATTEMPTS_PER_INTENT",
    "ALLOW_INSECURE_HTTP",
    "MAX_IMAGE_PIXELS",
    "MAX_IMAGE_WIDTH",
    "MAX_IMAGE_HEIGHT",
    "MAX_INSPECTION_BYTES",
    "TOKEN_TTL_SECONDS",
    "DOWNLOAD_URL_TTL_SECONDS",
    "CLEANUP_BATCH_SIZE",
    "CLEANUP_MIN_AGE_SECONDS",
    "CLEANUP_LEASE_SECONDS",
    "CLEANUP_FAILURE_COOLDOWN_SECONDS",
    "TERMINAL_RETENTION_SECONDS",
    "DELETE_REPLACED_FILES",
}


def _docs_text() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in DOC_PATHS)


def _mentions_export(docs_text: str, export_name: str) -> bool:
    return (
        re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(export_name)}(?![A-Za-z0-9_])", docs_text
        )
        is not None
    )


def _public_exports() -> dict[str, list[str]]:
    snapshot_exports = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    exports = {
        module_name: list(module_exports)
        for module_name, module_exports in snapshot_exports.items()
    }
    for module_name in ADDITIONAL_PUBLIC_MODULES:
        module = import_module(module_name)
        exports[module_name] = list(module.__all__)
    return exports


def test_unpublished_superpowers_docs_are_excluded_from_docs_corpus() -> None:
    assert not any(
        path.relative_to(DOCS_ROOT).as_posix().startswith("superpowers/")
        for path in DOC_PATHS
        if path.is_relative_to(DOCS_ROOT)
    )


def test_every_public_export_is_mentioned_in_documentation() -> None:
    exports = _public_exports()
    docs_text = _docs_text()

    missing: dict[str, list[str]] = {}
    for module_name, module_exports in exports.items():
        missing_names = [
            export_name
            for export_name in module_exports
            if not _mentions_export(docs_text, export_name)
        ]
        if missing_names:
            missing[module_name] = missing_names

    assert missing == {}


def test_file_upload_docs_are_navigable_and_cover_every_setting() -> None:
    for path in UPLOAD_DOC_PATHS:
        assert path.is_file()

    local_guide = UPLOAD_DOC_PATHS[1].read_text(encoding="utf-8")
    missing_settings = sorted(
        name for name in UPLOAD_SETTING_NAMES if f"`{name}`" not in local_guide
    )
    assert missing_settings == []

    navigation = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    for path in UPLOAD_DOC_PATHS:
        relative = path.relative_to(DOCS_ROOT).as_posix()
        assert relative in navigation


def test_file_upload_guides_cover_required_security_and_operation_topics() -> None:
    corpus = "\n".join(path.read_text(encoding="utf-8") for path in UPLOAD_DOC_PATHS)
    required_terms = {
        "UploadToken",
        "StoredFile",
        "StoredImage",
        "FileUploadPolicy",
        "FileInspection",
        "register_upload_adapter",
        "cleanup_upload_intents",
        "PROCESSING",
        "FINALIZING",
        "SUPERSEDED",
        "VersionId",
        "SigV4",
        "BucketOwnerEnforced",
        "CORS",
        "DELETE_REPLACED_FILES",
        "X-Content-Type-Options",
        "cross-database",
        "multipart",
    }
    missing_terms = sorted(term for term in required_terms if term not in corpus)
    assert missing_terms == []
