# Use direct S3 uploads

GeneralManager automatically selects its version-aware S3 adapter when the
configured Django storage proves every required capability. If any check fails,
the same field remains usable through the authenticated proxy transfer path.

## Install and configure django-storages

```bash
pip install "general-manager[file-upload-s3]"
```

The extra installs `boto3>=1.42.0` and `django-storages[s3]>=1.14`. A private
Django 5.2 storage configuration can look like:

```python
STORAGES = {
    "default": {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": "acme-private-uploads",
            "region_name": "eu-central-1",
            "signature_version": "s3v4",
            "querystring_auth": True,
            "default_acl": None,
            "object_parameters": {
                "ServerSideEncryption": "aws:kms",
                "SSEKMSKeyId": "arn:aws:kms:eu-central-1:123456789012:key/…",
                "BucketKeyEnabled": True,
            },
        },
    },
}

GENERAL_MANAGER = {
    "FILE_UPLOADS": {
        "ENABLED": True,
        "STAGING_PREFIX": "gm-staging/",
        "MAX_BYTES": 100_000_000,
        "TOKEN_TTL_SECONDS": 900,
        "DOWNLOAD_URL_TTL_SECONDS": 300,
    }
}
```

Use normal IAM/environment credentials; never put secrets in adapter identity
values or application logs. The adapter fingerprint includes only the backend,
bucket, safe endpoint, and other allowlisted non-secret identity.

## Required bucket and client capabilities

Direct mode is enabled only when all of these are verified:

- bucket versioning reports `Enabled` (not merely configured on the Python
  object);
- the boto client explicitly uses SigV4;
- the SDK operation model supports destination `IfNoneMatch` for conditional
  `CopyObject`;
- staged objects are private;
- the S3 client can return `VersionId`, `ETag`, SHA-256 checksum, size, and
  content type through `HeadObject` with checksum mode;
- the endpoint is AWS S3 over HTTPS, or a custom S3-compatible storage explicitly
  declares `supports_conditional_copy=True` and implements the same semantics.

Enable versioning, for example:

```bash
aws s3api put-bucket-versioning \
  --bucket acme-private-uploads \
  --versioning-configuration Status=Enabled
```

Prefer S3 Object Ownership `BucketOwnerEnforced`. With it, omit ACL settings
entirely (`default_acl=None` and no `ACL` in `object_parameters`) because S3
rejects ACL headers. If an older cross-account policy requires
`bucket-owner-full-control`, GeneralManager can sign that one ACL, but ownership
and bucket policy must be tested explicitly.

Allowed `object_parameters` are `ACL`, `ServerSideEncryption`, `SSEKMSKeyId`,
`BucketKeyEnabled`, and `StorageClass`. Unknown options fail direct capability
checks. `SSEKMSKeyId` and `BucketKeyEnabled` require
`ServerSideEncryption="aws:kms"`. For a storage configured as public, direct
staging is rejected unless the storage explicitly declares
`upload_staging_prefix_private=True`; the staging prefix itself must remain
private even when final objects are public.

The application role needs the narrow equivalent of:

- `s3:GetBucketVersioning`;
- `s3:HeadObject`/`s3:GetObjectVersion` for exact validation and download;
- `s3:PutObject` for signed staging and conditional final copies;
- `s3:DeleteObjectVersion` for exact cleanup;
- KMS encrypt/decrypt/data-key permissions when SSE-KMS is configured.

Scope permissions to the configured staging and final prefixes. Do not grant
bucket listing merely for this workflow.

## Browser CORS

Direct upload is one presigned `PUT`, not a POST form and not multipart upload.
The browser must be allowed to send every signed header. A restrictive bucket
CORS example is:

```json
[
  {
    "AllowedOrigins": ["https://app.example.com"],
    "AllowedMethods": ["PUT", "GET", "HEAD"],
    "AllowedHeaders": [
      "content-type",
      "x-amz-checksum-sha256",
      "x-amz-server-side-encryption",
      "x-amz-server-side-encryption-aws-kms-key-id",
      "x-amz-server-side-encryption-bucket-key-enabled"
    ],
    "ExposeHeaders": ["etag", "x-amz-version-id", "x-amz-checksum-sha256"],
    "MaxAgeSeconds": 300
  }
]
```

Remove headers your configuration does not sign, add the exact signed storage
headers it does use, and list only real application origins. Wildcard origins
are inappropriate for credential-bearing application traffic.

## Execute the transfer

Call `beginFileUpload` exactly as in the
[local guide](graphql_file_uploads.md). A direct response resembles:

```json
{
  "token": "opaque-consumption-token",
  "transport": "DIRECT",
  "uploadUrl": "https://acme-private-uploads.s3.eu-central-1.amazonaws.com/…",
  "method": "PUT",
  "headers": [
    {"name": "Content-Type", "value": "image/png"},
    {"name": "Content-Length", "value": "184320"},
    {"name": "x-amz-checksum-sha256", "value": "base64-digest"}
  ]
}
```

Send the exact file body, method, URL, and signed headers without adding an
application `Authorization` header or cookies. `Content-Length` is a forbidden
script header in Fetch: validate it against `file.size`, omit it from the
JavaScript header object, and let the browser derive it from the `Blob` body.

```javascript
const contentLength = instructions.headers.find(
  ({name}) => name.toLowerCase() === "content-length",
);
if (contentLength && Number(contentLength.value) !== file.size) {
  throw new Error("Upload size changed after signing");
}
const headers = Object.fromEntries(instructions.headers
  .filter(({name}) => name.toLowerCase() !== "content-length")
  .map(({name, value}) => [name, value]));
const response = await fetch(instructions.uploadUrl, {
  method: instructions.method,
  mode: "cors",
  credentials: "omit",
  headers,
  body: file,
});
if (!response.ok) throw new Error(`S3 PUT failed: ${response.status}`);
```

Then pass `token` to the generated mutation. GeneralManager performs
`HeadObject`, records the immutable `VersionId`, validates that exact version,
and conditionally copies it to the UUID-qualified `upload_to` result with source
`VersionId`, source `ETag`, destination `IfNoneMatch: *`, intent/checksum
metadata, and checksum calculation. A client overwriting the staging key creates
a new version; it cannot change the version already validated and claimed.

Direct uploads are limited to S3's single-`PUT` maximum of 5 GiB even if
`MAX_BYTES` is higher. V1 has no S3 multipart or resumable upload. SigV4 URLs and
`DOWNLOAD_URL_TTL_SECONDS` are capped at 604,800 seconds; use much shorter values
in practice. The signed `Content-Length`, content type, checksum, and encryption
headers must match exactly. The browser/network stack supplies the signed
`Content-Length`; it is not a client-controlled CORS header.

## Exact downloads and residual capabilities

Private structured output contains a presigned `GetObject` URL with the retained
final `VersionId`, response content type, and safe inline disposition. Before
issuing it, GeneralManager heads that exact version and verifies checksum and
size. The URL reveals S3 request details, so treat it as a secret and never log
or persist it.

S3 cannot revoke a presigned URL already issued. After replacement, deletion,
or a permission change, it may remain usable until its short TTL expires. Local
application capabilities recheck the current row/key/version, so replacement or
deletion invalidates them, but possession is sufficient and a pure permission
change also leaves access until the local capability TTL expires. Versioning
also retains old versions until
your safe cleanup and bucket lifecycle rules remove them; ensure lifecycle age
exceeds active upload/download/reconciliation windows and never expire the
staging prefix before GeneralManager can recover `FINALIZING` rows.

Public mode requires explicit public storage configuration and is unsuitable for
access-controlled images. Newly consumed uploads return a public URL only when
django-storages preserves exactly one matching `versionId` query on the native,
verified S3 service endpoint. The built-in S3 adapter rejects every configured
`custom_domain` and any unexpected host, even if its URL happens to preserve the
query, because GeneralManager cannot prove a CDN implements S3 version semantics.
It never appends the query blindly. A project with a version-aware CDN must
register a custom `ExactPublicDownloadAdapter` and contract-test immutability.
The accepted native AWS URL must be HTTPS on the effective default port and name
the configured bucket on an S3 service host. An explicitly supported custom S3
endpoint must match the configured scheme, hostname, and effective port exactly.
The accepted URL is non-expiring and identifies the immutable version.

## Proxy fallback

When versioning is disabled/unverifiable or conditional copy is unavailable,
GeneralManager selects the distinct `s3-proxy` adapter. The authenticated Django
endpoint spools and verifies the bounded request, then uses S3 `PutObject
IfNoneMatch: *`, `GetObject IfMatch`, and `DeleteObject IfMatch`. Finalization
streams the exact staged ETag through the server into a conditionally created
destination; it never falls back to an `exists()`/`save()` overwrite race.
Replacement cleanup likewise deletes only the previously recorded ETag. The
adapter identity is persisted as `s3-proxy` so retries resolve the same contract
after a restart.

If bucket versioning is enabled but another direct prerequisite (such as
conditional copy) is missing, `s3-proxy` retains every returned `VersionId` and
uses version-specific head/get/delete operations. Cleanup therefore removes the
exact staged or replaced version rather than merely adding a delete marker.
Truly unversioned buckets use the recorded ETag plus SHA-256 and conditional
`IfMatch` operations instead.

The proxy fallback still requires an HTTPS S3 endpoint, an explicitly configured
SigV4 client, SHA-256 metadata, and SDK support for those conditional put/get/delete
members. Custom endpoints must also opt in with
`supports_conditional_copy=True` as an explicit statement that their conditional
S3 semantics are compatible. Public staging, unsupported object options, missing
optional dependencies, unsafe transport/signing, or absent conditional operations
fail closed instead of using Django storage's potentially overwriting `save()`.
Keep the proxy route reachable and sized in the reverse proxy even when S3
normally operates in direct mode.

`python manage.py check` may emit `general_manager.uploads.W001` when static
inspection cannot prove runtime capabilities. It emits an `E004`/`E005` error
when safe finalization or requested public URL capability is known to be absent.

## Troubleshooting

- **Browser CORS failure before a response**: verify the exact origin, `PUT`, and
  every returned script-settable header. Do not add `Content-Length`; Fetch
  supplies it. Browser developer tools may hide the S3 XML body when preflight
  itself fails.
- **`SignatureDoesNotMatch`**: do not recompute, reorder semantically, remove, or
  add signed headers; check region/endpoint, SigV4, clock skew, content length,
  content type, checksum base64, and proxy/CDN header rewriting.
- **`AccessDenied`**: check bucket policy, object ownership, KMS grants, staging
  prefix, conditional copy, and version-specific get/delete permissions.
- **Response says `PROXY`**: this may be the safe `s3-proxy` fallback. Verify
  bucket versioning through the actual role, inspect boto's signature version
  and Put/Get/Delete operation models, and confirm a custom endpoint declares
  compatible conditional semantics.
- **`UPLOAD_STORAGE_CHANGED`**: storage configuration/fingerprint changed or an
  exact version/metadata no longer matches. Restore the original configuration
  and reconcile; never point the intent at a different bucket/version.
- **`UPLOAD_CHECKSUM_MISMATCH`**: hash exactly the bytes sent, then encode the
  returned `x-amz-checksum-sha256` value exactly; do not confuse hex and base64.
- **`PROCESSING` or `FAILED`**: run `cleanup_upload_intents`, verify conditional
  copy and `HeadObject` checksum support, and keep the staged version until the
  intent reaches `CONSUMED` or `SUPERSEDED`.
