# Upload files through GraphQL

This guide configures private local-file uploads end to end. The same GraphQL
calls work with other Django storages; only the transfer instructions change.

## Prerequisites

Install GeneralManager's normal Django dependencies and Pillow when using
`ImageField`. Apply the internal upload-intent migration in every environment:

```bash
python manage.py migrate
```

Serve the application over HTTPS. Plain HTTP transfer/download URLs are rejected
unless Django `DEBUG=True` **and** `ALLOW_INSECURE_HTTP=True`; that combination is
for local development only. Configure Django authentication middleware so both
GraphQL and proxy `PUT` requests have `request.user`.

## Configure uploads

```python
GENERAL_MANAGER = {
    "FILE_UPLOADS": {
        "ENABLED": True,
        "HTTP_UPLOAD_PATH": "gm/uploads/",
        "STAGING_PREFIX": "gm-staging/",
        "INTENT_DATABASE": "default",
        "MAX_BYTES": 25_000_000,
        "TOKEN_TTL_SECONDS": 900,
        "DOWNLOAD_URL_TTL_SECONDS": 300,
        "DELETE_REPLACED_FILES": False,
    }
}
```

Bootstrap adds `PUT /gm/uploads/<intent UUID>` and
`GET|HEAD /gm/uploads/download/<signed capability>` before the project URL list.
Startup fails on a collision. Disabling uploads while an exposed manager has an
editable ORM file field also fails startup; it never restores unsafe arbitrary
string assignment.

### Complete settings reference

Every integer below must be a positive non-boolean integer. Unknown keys are
rejected. Both path settings must be safe relative paths ending in `/`.

| Setting | Default | Meaning and constraints |
| --- | ---: | --- |
| `ENABLED` | `False` | Registers the mutation and HTTP routes. |
| `HTTP_UPLOAD_PATH` | `"gm/uploads/"` | Framework-owned relative route prefix. |
| `STAGING_PREFIX` | `"gm-staging/"` | Private, framework-owned storage prefix. |
| `INTENT_DATABASE` | `"default"` | Intent DB alias; `null`/empty normalizes to `default`. Must equal each manager's effective alias. |
| `MAX_BYTES` | `25_000_000` | Global per-file declared/verified byte ceiling. |
| `MAX_PENDING_INTENTS_PER_USER` | `20` | Active intents admitted per owner. |
| `MAX_PENDING_BYTES_PER_USER` | `100_000_000` | Sum of active declared bytes per owner. |
| `MAX_PENDING_INTENTS_GLOBAL` | `1_000` | Active intents across all owners. |
| `MAX_PENDING_BYTES_GLOBAL` | `5_000_000_000` | Active declared bytes globally. |
| `BEGIN_RATE_LIMIT_WINDOW_SECONDS` | `60` | Fixed begin-upload counter window. |
| `MAX_BEGIN_ATTEMPTS_PER_USER` | `30` | Begin attempts per owner/window. |
| `MAX_BEGIN_ATTEMPTS_GLOBAL` | `1_000` | Begin attempts globally/window. |
| `TRANSFER_LEASE_SECONDS` | `60` | Proxy lease duration before a crashed transfer may be reclaimed. |
| `TRANSFER_CREDENTIAL_TTL_SECONDS` | `900` | Lifetime of the distinct proxy transfer credential. |
| `TRANSFER_RATE_LIMIT_WINDOW_SECONDS` | `60` | Fixed proxy-attempt counter window. |
| `MAX_TRANSFER_ATTEMPTS_PER_USER` | `120` | Proxy attempts per owner/window. |
| `MAX_TRANSFER_ATTEMPTS_GLOBAL` | `5_000` | Proxy attempts globally/window. |
| `MAX_TRANSFER_ATTEMPTS_PER_INTENT` | `10` | Durable attempt ceiling for one intent. |
| `ALLOW_INSECURE_HTTP` | `False` | Permits HTTP only while Django `DEBUG=True`. |
| `MAX_IMAGE_PIXELS` | `40_000_000` | Decoded image pixel ceiling. |
| `MAX_IMAGE_WIDTH` | `16_384` | Decoded image width ceiling. |
| `MAX_IMAGE_HEIGHT` | `16_384` | Decoded image height ceiling. |
| `MAX_INSPECTION_BYTES` | `1_048_576` | Maximum prefix supplied to a content inspector. |
| `TOKEN_TTL_SECONDS` | `900` | Consumption-intent lifetime. |
| `DOWNLOAD_URL_TTL_SECONDS` | `300` | Private URL lifetime, maximum `604_800` (seven days). |
| `CLEANUP_BATCH_SIZE` | `100` | Default bounded maintenance batch. |
| `CLEANUP_MIN_AGE_SECONDS` | `3_600` | Default age for expiry/terminal cleanup. |
| `CLEANUP_LEASE_SECONDS` | `300` | Durable reconciliation worker lease. |
| `CLEANUP_FAILURE_COOLDOWN_SECONDS` | `60` | Delay before retrying a failed cleanup/finalization. |
| `TERMINAL_RETENTION_SECONDS` | `86_400` | Retention for terminal intent metadata; must exceed the download URL TTL. |
| `DELETE_REPLACED_FILES` | `False` | Deletes an old object only after safe exact claim and successful replacement. |

When enabling local replacement deletion, reserve
`gm-upload-old-claims/` exclusively for GeneralManager. Do not write, sync,
restore, scan-and-rewrite, or manually clean paths below it while the
application is running. GeneralManager serializes its own workers with a
durable cleanup lease and re-verifies moved inode/checksum identities, but
portable POSIX filesystems have no atomic compare-and-unlink primitive. If
another process can mutate that namespace, keep `DELETE_REPLACED_FILES=False`.

`TERMINAL_RETENTION_SECONDS` is a minimum age, not permission to discard live
download metadata. A `CONSUMED` intent is retained beyond that age for as long as
the current model row still references its `final_key`; cleanup deletes it only
after replacement, explicit clear, or row deletion removes that live binding.
This preserves exact-version verification for structured downloads.

The default cache backend must implement atomic `add` and `incr`; unsafe
fallback implementations fail closed with `UPLOAD_STORAGE_ERROR`. Admission is
also serialized through the upload quota-lock database row. Authentication is
validated before either global or per-user admission counters are incremented,
so anonymous traffic cannot consume the authenticated global upload budget.

## Define file fields and policies

```python
from django.db import models

from general_manager import GeneralManager
from general_manager.api import FileInspection, FileUploadPolicy
from general_manager.interface import DatabaseInterface


def detect_pdf(value: FileInspection) -> str | None:
    # `content` is at most MAX_INSPECTION_BYTES and may be only a prefix.
    return "application/pdf" if value.content.startswith(b"%PDF-") else None


def avatar_path(instance, filename: str) -> str:
    # Scalar values are assigned before generate_filename calls this function.
    return f"profiles/{instance.account_slug}/{filename}"


class Profile(GeneralManager):
    class Interface(DatabaseInterface):
        account_slug = models.SlugField()
        resume = models.FileField(upload_to="resumes/", blank=True)
        avatar = models.ImageField(upload_to=avatar_path, blank=True)

    class FileUploads:
        fields = {
            "resume": FileUploadPolicy(
                max_bytes=8_000_000,
                allowed_content_types=("application/pdf",),
                allowed_extensions=(".pdf",),
                content_inspector=detect_pdf,
                public=False,
            ),
            "avatar": FileUploadPolicy(
                max_bytes=5_000_000,
                allowed_content_types=("image/jpeg", "image/png"),
                allowed_extensions=(".jpg", ".jpeg", ".png"),
                public=False,
            ),
        }
```

Unlisted ORM file fields inherit the global maximum and private mode. Policy
objects are immutable. Allowlists must be non-empty sequences; content types
must be syntactically valid and image policies may contain only `image/*` types.
Extensions may be written with a leading dot. The client media type and suffix
still do not prove content. `ImageField` is always decoded; use a
`FileContentInspector` for strict general-file recognition.

For a non-image `FileField`, configuring `allowed_content_types` requires a
`content_inspector`; otherwise consumption fails with
`UPLOAD_BACKEND_UNSUPPORTED` because declared/storage metadata alone is not
content proof. The inspector must return a normalized detected MIME string that
matches the exact staged object's recorded type. `None` or any non-string result
becomes `INVALID_FILE_TYPE`; an unexpected inspector exception is sanitized as
`UPLOAD_STORAGE_ERROR`.

## GraphQL contract

The generic mutation is:

```graphql
mutation BeginAvatar($inputSize: BigIntScalar!, $digest: String!, $objectId: ID) {
  beginFileUpload(
    manager: "Profile"
    field: "avatar"
    operation: UPDATE
    objectId: $objectId
    filename: "portrait.png"
    size: $inputSize
    contentType: "image/png"
    checksum: {algorithm: SHA256, digest: $digest}
  ) {
    token
    transport
    uploadUrl
    method
    headers { name value }
    expiresAt
  }
}
```

`operation` is required. `objectId` is forbidden for `CREATE` and required for
`UPDATE`; it is not inferred. `size` is `BigIntScalar` and should be sent as a
decimal string. The checksum is always required in v1 and is a lowercase hex or
accepted SHA-256 encoding normalized by the server. Use the manager attribute
name registered by the generated interface for `field` (for example `avatar`;
Python attributes containing underscores remain snake_case here).

Consume the token with variables, never by interpolating it into query text:

```graphql
mutation UpdateAvatar($id: Int!, $upload: UploadToken!) {
  updateProfile(id: $id, avatar: $upload) {
    success
    Profile {
      id
      avatar {
        name originalName contentType size checksum
        width height status downloadUrl expiresAt
      }
    }
  }
}
```

For `blank=True`, `avatar: null` clears the field and omitting `avatar` leaves it
unchanged. For `blank=False`, generated create input is required when the Django
field has no default and explicit null is rejected.

## Browser workflow

The following uses Web Crypto for SHA-256 and preserves every script-settable
header exactly as returned. It validates but omits the forbidden `Content-Length`
header so Fetch can derive it from the body. Do not log the token, transfer
headers, or signed URLs.

```javascript
const graphql = async (query, variables) => {
  const response = await fetch("/graphql/", {
    method: "POST",
    credentials: "same-origin",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({query, variables}),
  });
  const payload = await response.json();
  if (payload.errors) throw payload.errors[0];
  return payload.data;
};

const hex = bytes => [...new Uint8Array(bytes)]
  .map(value => value.toString(16).padStart(2, "0")).join("");

async function uploadAvatar(profileId, file) {
  const digest = hex(await crypto.subtle.digest("SHA-256", await file.arrayBuffer()));
  const begun = (await graphql(`
    mutation Begin($id: ID!, $name: String!, $size: BigIntScalar!, $type: String!, $digest: String!) {
      beginFileUpload(manager: "Profile", field: "avatar", operation: UPDATE,
        objectId: $id, filename: $name, size: $size, contentType: $type,
        checksum: {algorithm: SHA256, digest: $digest}) {
        token transport uploadUrl method headers { name value } expiresAt
      }
    }`, {id: String(profileId), name: file.name, size: String(file.size),
          type: file.type, digest})).beginFileUpload;

  const contentLength = begun.headers.find(
    ({name}) => name.toLowerCase() === "content-length",
  );
  if (contentLength && Number(contentLength.value) !== file.size) {
    throw new Error("Upload size changed after beginFileUpload");
  }
  // Content-Length is a forbidden Fetch header. The browser derives it from
  // the Blob body; preserve every other returned header exactly.
  const headers = Object.fromEntries(begun.headers
    .filter(({name}) => name.toLowerCase() !== "content-length")
    .map(({name, value}) => [name, value]));
  const transferred = await fetch(begun.uploadUrl, {
    method: begun.method,
    credentials: begun.transport === "PROXY" ? "same-origin" : "omit",
    headers,
    body: file,
  });
  if (!transferred.ok) throw await transferred.json();

  return (await graphql(`
    mutation Consume($id: Int!, $token: UploadToken!) {
      updateProfile(id: $id, avatar: $token) {
        Profile { avatar { status downloadUrl expiresAt } }
      }
    }`, {id: Number(profileId), token: begun.token})).updateProfile.Profile;
}
```

Bind an image only while available:

```javascript
const profile = await uploadAvatar(42, input.files[0]);
if (profile.avatar.status === "AVAILABLE") {
  image.src = profile.avatar.downloadUrl;
} else {
  // Poll the normal profile query. Never replay the consumed upload token.
  scheduleProfilePoll(42);
}
```

Representative GraphQL failure:

```json
{
  "errors": [{
    "message": "The file upload could not be completed.",
    "extensions": {"code": "UPLOAD_EXPIRED"}
  }],
  "data": {"updateProfile": null}
}
```

The proxy endpoint instead returns a small HTTP envelope, for example:

```json
{"error":{"code":"UPLOAD_CHECKSUM_MISMATCH","message":"The uploaded checksum did not match."}}
```

It uses 401 for missing/invalid credentials, 404 for non-enumerating absence,
409 for transfer replay/conflict, 410 for expiry, 413/422 for size/checksum,
415 for type, 429 for rate limiting, and 503 for storage/configuration failure.

## Stable GraphQL error codes

| Code | Meaning |
| --- | --- |
| `UNAUTHENTICATED` | No durable authenticated owner. |
| `UPLOAD_MANAGER_INVALID`, `UPLOAD_FIELD_INVALID` | Destination is not exposed/editable. |
| `UPLOAD_OPERATION_INVALID`, `UPLOAD_TARGET_UNAVAILABLE` | Invalid create/update shape or unreadable target. |
| `INVALID_UPLOAD_FILENAME`, `INVALID_UPLOAD_SIZE`, `INVALID_UPLOAD_CHECKSUM` | Begin input failed validation. |
| `UPLOAD_QUOTA_EXCEEDED`, `UPLOAD_RATE_LIMITED` | Admission/rate budget exhausted. |
| `UPLOAD_EXPIRED`, `UPLOAD_TOKEN_INVALID`, `UPLOAD_INCOMPLETE` | Token is expired, invalid, or transfer is unfinished. |
| `UPLOAD_ALREADY_CONSUMED`, `UPLOAD_TRANSFER_CONFLICT`, `UPLOAD_SUPERSEDED` | Replay, concurrent transfer, or later update won. |
| `UPLOAD_BINDING_MISMATCH` | Owner/manager/field/operation/target binding differs. |
| `UPLOAD_SIZE_MISMATCH`, `UPLOAD_CHECKSUM_MISMATCH` | Actual immutable bytes differ. |
| `INVALID_FILE_TYPE`, `INVALID_IMAGE` | Content policy or image decoding failed. |
| `UPLOAD_BACKEND_UNSUPPORTED`, `UPLOAD_STORAGE_CHANGED`, `UPLOAD_DATABASE_MISMATCH` | Required safe backend/database guarantees are unavailable. |
| `UPLOAD_FINALIZATION_FAILED`, `UPLOAD_STORAGE_ERROR` | Durable finalization or storage failed safely. |
| `PERMISSION_DENIED` | The manager's normal complete-payload permission check denied mutation. |

These correspond to the public `UploadError` subclasses documented in the
[GraphQL API reference](../api/graphql.md).

## Reconciliation and cleanup

Run maintenance frequently enough that failed finalization and abandoned stages
do not accumulate:

```bash
python manage.py cleanup_upload_intents --dry-run
python manage.py cleanup_upload_intents --batch-size 200 --older-than 3600
```

Both numeric options must be positive. Output contains only safe counts:
`reconciled`, `expired`, `cleaned`, `deleted`, `failed`, and `skipped`.
Retention cleanup rechecks the canonical model binding under lock and skips an
aged `CONSUMED` row while that field still points at its final key.

Cron example (every five minutes):

```cron
*/5 * * * * cd /srv/app && /srv/venv/bin/python manage.py cleanup_upload_intents --batch-size 200
```

Kubernetes CronJob command fragment:

```yaml
schedule: "*/5 * * * *"
jobTemplate:
  spec:
    template:
      spec:
        restartPolicy: OnFailure
        containers:
          - name: upload-cleanup
            image: your-app:current
            args: ["python", "manage.py", "cleanup_upload_intents", "--batch-size", "200"]
```

Multiple workers use durable leases and bounded batches, but schedule overlap
should still be avoided where practical.

## Checks and metrics

Django checks use `general_manager.uploads.E000` through `E006` for invalid
settings, database mismatch, unsafe retention, policy/manager problems, missing
finalization/public capabilities, and disabled editable fields.
`general_manager.uploads.W001` means adapter capabilities require runtime
validation. Run `python manage.py check` in deployment configuration.

The no-op metrics backend emits nothing until an integration installs one.
Internal metric names are `upload_transition_total`, `upload_bytes`,
`upload_failure_total`, `upload_cleanup_total`, and `upload_duration_seconds`.
Labels are bounded adapter/state/transport/operation/result/error values; byte
counts and durations are values, never high-cardinality labels. Structured logs
may include intent UUID, adapter, manager, field, state and safe sizes, but never
tokens, digests, authorization headers, signed URLs, or raw storage keys.

## Custom storage adapters

The supported import seam is `general_manager.api`:

```python
from django.core.files.storage import Storage
from general_manager.api import (
    ClaimedObject,
    ExactPublicDownloadAdapter,
    ObjectVersion,
    ProxyUploadSink,
    UploadAdapter,
    UploadAdapterFactory,
    UploadFinalizationAdapter,
    UploadInstructions,
    UploadStorageError,
    UploadTransport,
    register_upload_adapter,
)


class AcmeStorage(Storage):
    ...


def build_acme(storage: Storage) -> UploadAdapter:
    return AcmeUploadAdapter(storage)


register_upload_adapter(AcmeStorage, build_acme)
```

Register during application startup before intents are created. A duplicate
storage class is rejected; the most-specific class wins. The factory must return
a runtime-compatible `UploadAdapter` with a stable safe `adapter_id`, positive
integer `adapter_version`, deterministic credential-free fingerprint, transfer
instructions, exact stage inspection/open/delete, conditional non-overwriting
materialization, exact private/public download operations, and no GraphQL or
permission dependency.

Post-commit and replacement support is the `UploadFinalizationAdapter` contract:
`inspect_materialized`, `delete_materialized`, `delete_object`,
`inspect_replaced_object`, `plan_replaced_object_claim`,
`claim_replaced_object`, and `delete_claimed_object`. Proxy streaming adapters
also implement `ProxyUploadSink.save_stage`. `UploadInstructions`,
`ObjectVersion`, and `ClaimedObject` are immutable boundary values. Raise a
documented framework `UploadError` such as `UploadStorageError` or
`UploadBackendUnsupportedError`; arbitrary exceptions are sanitized. Raise the
public `UploadObjectMissingError` only when an exact inspect/delete can prove
that the object is already absent. Reconciliation treats that signal as
idempotent cleanup success, while direct preflight maps it to the generic
`UPLOAD_INCOMPLETE` client error.

An adapter that exposes newly retained files publicly implements
`ExactPublicDownloadAdapter.public_download_url(key, version=...)`. The returned
URL must be unsigned and immutable. A queryless URL is allowed only when the
backend truly encodes the version in its path; S3 must return exactly one
`versionId` query matching `ObjectVersion.version_id`. Credential-bearing,
fragmented, user-info, mismatched, or extra-query URLs are rejected.

For static startup-check precision, a factory may publish a mapping attribute
named `upload_adapter_capabilities` with exactly `adapter_id`, `adapter_version`,
`finalization`, and `public`. Contract-test version races, conditional
no-overwrite, retries, missing objects, exact delete, URL credential safety, and
redaction. Internal intent models, token helpers, registries, and staging helpers
are deliberately not public API.

## Troubleshooting

- **`UPLOAD_EXPIRED`**: begin again; do not extend or reuse the old token.
- **`UPLOAD_INCOMPLETE`**: confirm the transfer returned success before mutation.
- **Checksum/size mismatch**: hash the exact bytes sent and preserve every
  returned request header. Avoid browser transformations between hashing and PUT.
- **`INVALID_IMAGE`**: decode the original locally, check pixel/dimension limits,
  and ensure the upload is not truncated or a mislabeled non-image.
- **`INVALID_FILE_TYPE`**: compare the declared type, allowlists, extension, and
  configured inspector result. A browser's `file.type` is not proof.
- **`PROCESSING`/`FAILED` persists**: run `cleanup_upload_intents`, inspect
  credential-redacted application logs, and verify final-key create/copy/delete
  permissions.
- **No public URL**: private is the default. `public=True` additionally requires
  a genuinely public adapter. Retained consumed uploads also require
  `ExactPublicDownloadAdapter`; without an exact immutable public capability they
  fail closed. S3 exact public URLs must preserve the matching `versionId`.
- **Custom storage uses proxy**: this is the safe fallback. Register a custom
  adapter only if the backend can meet exact-version/finalization contracts.
- **Database check failure**: set the manager interface and
  `INTENT_DATABASE` to the same alias. Cross-database uploads are unsupported.

## Migrating from file strings

This pre-stable feature intentionally replaces generated `String` input/output
for ORM `FileField` and `ImageField`. Clients must stop sending paths or URLs,
use `UploadToken` variables, and select nested structured fields. Existing rows
need no data migration; their names remain in Django's normal columns. Ordinary
`CharField`, `URLField`, `FilePathField`, request/remote fields, filters, and
custom string mutations remain strings.
