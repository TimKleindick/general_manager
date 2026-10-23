# Use S3 uploads through the authenticated proxy

New S3 upload intents use `s3-proxy` and the authenticated Django transfer
endpoint. The built-in `s3-versioned` adapter no longer issues presigned PUTs,
even when explicitly registered or called directly. Registry admission also
converts registered built-in direct adapters and subclasses to `s3-proxy`.
Arbitrary custom adapters remain a trusted extension point.

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

## Required storage capabilities

The S3 proxy requires an HTTPS endpoint, explicitly configured SigV4 client,
private staging, SHA-256 metadata, and SDK support for `PutObject IfNoneMatch`,
`GetObject IfMatch`, and `DeleteObject IfMatch`. Known S3 backends missing these
capabilities fail closed; they do not fall back to Django's generic `save()`.
Custom endpoints must declare `supports_conditional_copy=True` to attest their
conditional S3 semantics. Bucket versioning and conditional CopyObject are not
required for new proxy intents.

Allowed `object_parameters` remain `ACL`, `ServerSideEncryption`, `SSEKMSKeyId`,
`BucketKeyEnabled`, and `StorageClass`. KMS options require `aws:kms`. Prefer
`default_acl=None` for BucketOwnerEnforced storage. Public storage requires an
explicit `upload_staging_prefix_private=True` declaration and a genuinely
private staging prefix. Unknown or unsafe options fail closed.

The application role needs put, head/get, and exact delete access for the staging
and final prefixes, version-specific access when versioning is enabled, and KMS
permissions when configured. Existing legacy direct intents also retain their
versioning and conditional-copy requirements. This change requires no bucket
migration and does not change bucket configuration automatically.

## Execute the transfer

Call `beginFileUpload` as in the [local guide](graphql_file_uploads.md). New S3
instructions return `transport: PROXY`, `method: PUT`, an application upload URL,
and an `Authorization: GMUpload …` credential. Use all returned headers and the
same authenticated application session; the credential is bound to the owner,
intent, and adapter. Do not send it to a storage endpoint.

```javascript
const response = await fetch(instructions.uploadUrl, {
  method: instructions.method,
  credentials: "same-origin", // Cross-origin session cookies require "include".
  headers: Object.fromEntries(instructions.headers.map(({name, value}) => [name, value])),
  body: file,
});
if (!response.ok) throw new Error(`Upload failed: ${response.status}`);
```

Then pass the separate consumption token to the generated mutation. The transfer
endpoint claims the intent before reading the body, renews its bounded lease,
checks declared size and checksum, and records the exact staged object. Concurrent
active claims and replay after completion are rejected before another storage
write. Failed attempts use separate staging keys so delayed cleanup cannot delete
a later attempt's object. S3 writes use `IfNoneMatch: *`; finalization streams the
verified stage to a conditionally created destination.

Versioned buckets retain returned VersionIds for exact head/get/delete. Unversioned
buckets use ETag plus SHA-256 and conditional operations. Cleanup does not rely on
an `exists()`/`save()` sequence.

## Capacity and browser migration

Upload bytes now traverse the application server. Size the application workers,
request timeouts, reverse-proxy body limit, bandwidth, and temporary disk for
`FILE_UPLOADS.MAX_BYTES` and expected concurrency. The S3 adapter spools up to
1 MiB in memory before using temporary disk; the HTTP stack may buffer separately.
Keep admission quotas, transfer rate limits, lease duration, and declared-size
checks enabled. There is no multipart or resumable upload support.
The adapter rejects declared
sizes above the [S3 single-PUT limit](https://docs.aws.amazon.com/AmazonS3/latest/userguide/upload-objects.html)
of 5 GiB (5,368,709,120 bytes) when issuing instructions and before opening a
spool, and also bounds actual streamed bytes. A larger global `MAX_BYTES` does
not increase this S3-specific limit; other storage backends remain unaffected.

Clients must follow the returned transport instead of assuming S3 URLs. Same-origin
uploads need no bucket PUT CORS configuration. Cross-origin application clients
need the application's existing authenticated CORS/CSRF configuration, including
the returned Authorization header, an explicit allowed origin and credentialed
requests. Use `credentials: "include"` for cross-origin session cookies; the
`GMUpload` header does not replace the authenticated session. Existing storage GET/download CORS requirements
are separate. Do not remove bucket permissions or CORS rules blindly during an
application upgrade.

## Existing intents and downloads

Persisted `s3-versioned` identities remain resolvable for preflight, exact-version
validation, finalization, downloads, and cleanup. They cannot obtain new or renewed
PUT credentials from the built-in adapter. Already issued presigned PUT URLs are
**not revoked**: they retain their old expiry and replay exposure. This patch does
not claim to remove previously created versions or to retroactively secure those
URLs. Plan any legacy remediation separately.

New private proxy downloads use the application capability endpoint and exact
object verification. Legacy direct private downloads can still issue exact-VersionId
presigned GET URLs; these remain usable until expiry even after a permission
change. For explicitly public storage, versioned proxy objects retain structured public
download URLs using the same exact-VersionId and trusted-endpoint checks as the
legacy adapter. Missing or mutable `null` versions, custom domains, URLs that drop
the version, and unexpected endpoints fail closed. Unversioned proxy objects do
not expose structured public URLs. This does not make private storage public.

## Troubleshooting

- `PROXY` is the expected new S3 transport, not a capability failure.
- Admission storage errors: check SigV4, HTTPS, private staging and conditional
  put/get/delete SDK support; no weaker generic fallback is available.
- `UPLOAD_TRANSFER_CONFLICT`: an upload is active or already completed; inspect
  its state instead of retrying a completed transfer.
- `UPLOAD_CHECKSUM_MISMATCH`: hash exactly the bytes sent; admission uses SHA-256
  hexadecimal text. The server supplies S3 checksum metadata.
- `UPLOAD_STORAGE_CHANGED`: restore the original non-secret storage configuration
  and reconcile; never redirect a persisted intent to a different bucket.
