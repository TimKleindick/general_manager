# GraphQL file uploads and downloads

GeneralManager treats binary transfer and GraphQL mutation authorization as two
separate concerns. GraphQL creates a short-lived, field-bound upload intent and
later consumes its opaque token in a generated create or update mutation. The
bytes travel in a dedicated HTTP `PUT`; they are never embedded in GraphQL and
the GraphQL multipart request convention is not supported.

This design supports Django `FileField` and `ImageField`, keeps each field's
configured `Storage` and `upload_to`, and avoids accepting client-supplied
storage paths or URLs.

## Trust boundaries and lifecycle

1. An authenticated client calls `beginFileUpload`. GeneralManager validates the
   live manager, editable ORM file field, operation, update target, filename,
   declared size/type/checksum, rate limits, and pending quotas.
2. The response contains a one-time consumption token plus `DIRECT` or `PROXY`
   transfer instructions. Only a digest of the token is stored. Proxy transfer
   uses a different short-lived `Authorization: GMUpload ...` credential.
3. A proxy endpoint streams a bounded body into a server-generated staging key.
   A direct adapter, such as S3, signs one private staging key and later records
   its immutable object version.
4. The generated mutation replaces the token with a redacted upload candidate
   before the manager's normal permission check. A denied permission does not
   claim the token or create/update an ORM row.
5. Content is validated against the recorded exact version. Inside one short
   database transaction, GeneralManager locks the intent, calls Django's
   `field.generate_filename()` after scalar assignment, saves a UUID-qualified
   final name on the model, and moves the intent to `FINALIZING`.
6. An `on_commit` callback conditionally materializes that exact staged version.
   Success becomes `CONSUMED`; a storage failure remains durable `FINALIZING`
   and is retried by `cleanup_upload_intents`.

The externally useful states are represented by `StoredFileStatus`:

- `AVAILABLE`: exact retained bytes are available.
- `PROCESSING`: the row is committed and finalization is still pending.
- `FAILED`: finalization failed or the retained object cannot be verified.

Clients should poll the object's normal GraphQL query while status is
`PROCESSING` or `FAILED`; they must not replay the mutation token. A token is
single-use once its intent becomes `FINALIZING`.

## Consistency and replacement

The database and object storage cannot share a transaction. The durable
`FINALIZING` saga prevents a committed row from silently switching to different
bytes and makes process interruption recoverable. Adapters must materialize
without overwriting unrelated content and identify both the source and final
object exactly. A changed adapter ID, contract version, or non-secret storage
fingerprint fails closed.

Replacing a file does not delete the old key by default. With
`DELETE_REPLACED_FILES=True`, deletion is attempted only after the replacement
is `CONSUMED` and the row still references it. Exact deletion can still be
unsupported by an arbitrary storage backend; GeneralManager then retains the
old object. Shared keys cannot always be detected, so do not enable deletion if
records may deliberately share file names. Local storage uses exact inode and
checksum claims and refuses deletion when ownership cannot be proven.

`gm-upload-old-claims/` is a framework-exclusive local-storage namespace.
Server-derived claim paths plus the durable cleanup lease serialize
GeneralManager workers; application code, operators, sidecars, and other
processes must not create, replace, or delete anything below that prefix. POSIX
does not provide a portable compare-and-unlink operation, so the local adapter
moves a claim into that reserved namespace, re-verifies inode/checksum identity,
and then deletes under this explicit exclusivity contract. Deployments that
cannot reserve the namespace must leave `DELETE_REPLACED_FILES=False` or provide
a custom adapter with an atomic exact-delete primitive.

If another update or deletion wins before finalization completes, reconciliation
marks the intent `SUPERSEDED` and removes only objects it can prove belong to
that intent. A replacement also invalidates previously issued local download
capabilities because every download rechecks the current model field binding.

## Structured file values

When uploads are enabled, generated mutation inputs for ORM file fields use the
`UploadToken` scalar. Generated outputs are `StoredFile` or `StoredImage`, not
`String`:

```graphql
type StoredFile {
  name: String!
  originalName: String!
  size: BigIntScalar
  contentType: String
  checksum: String
  status: StoredFileStatus!
  downloadUrl: String
  expiresAt: DateTime
}

type StoredImage {
  name: String!
  originalName: String!
  size: BigIntScalar
  contentType: String
  checksum: String
  width: Int
  height: Int
  status: StoredFileStatus!
  downloadUrl: String
  expiresAt: DateTime
}
```

An empty Django file value resolves to `null`. Metadata that cannot be verified
for a pre-existing file is nullable. `BigIntScalar` serializes sizes as strings.
Resolvers perform storage work lazily and cache metadata for the current request.

On updates, omitting a file field preserves it. Explicit `null` clears only a
field declared with `blank=True`; `blank=False` rejects `null`. Django's `null`
option does not define file clearability.

## Validation

Filenames are normalized as Unicode and must be a single safe basename. Path
separators, drive prefixes, dot segments, control characters, ambiguous Unicode
normalization, empty names, and excessive length are rejected. Storage and final
keys are generated by the server.

The client-declared media type and extension are admission hints, not proof of
content. Every `ImageField` is decoded and verified, including pixel and
dimension limits and decompression-bomb handling. A general `FileField` uses
size/checksum/storage metadata plus any configured `FileContentInspector`.
Projects that need strong format recognition must supply an inspector. Its
`FileInspection.content` is bounded by `MAX_INSPECTION_BYTES`, may be truncated,
and deliberately contains no credentials or storage keys.

## Downloads

Files are private by default. Local storage returns a short-lived signed
application URL suitable for `<img src>`; possession of that capability is
sufficient until expiry, so keep the TTL short. The view rechecks signature,
expiry, manager/object/field, current key, retained intent, and exact version for
both `GET` and `HEAD`. Responses use `Content-Disposition: inline`,
`X-Content-Type-Options: nosniff`, and private cache controls without revealing a
filesystem path.

S3 returns a private presigned exact-`VersionId` URL. It cannot be revoked after issue,
so replacement or permission changes leave a residual capability window until
the configured TTL expires. `public=True` is accepted only when an adapter
explicitly declares a genuinely public URL capability. Legacy values use the
ordinary public URL. Retained consumed uploads additionally require
`ExactPublicDownloadAdapter`, which must bind the immutable version without
credentials. S3 accepts exactly one matching `versionId` query and rejects a
custom-domain/storage URL that drops it. Adapters lacking that exact capability
fail closed with `downloadUrl: null`. Public URLs have no expiry.

## Scope and limitations

- Upload-enabled managers must use the same database alias as
  `FILE_UPLOADS.INTENT_DATABASE`; cross-database sagas are not supported.
- Direct S3 requires versioning and safe conditional copy. When those two direct
  prerequisites are missing, the `s3-proxy` adapter can use authenticated,
  conditional put/get/delete operations without weakening exact-object or
  no-overwrite guarantees. Unsafe signing, transport, or conditional-operation
  capabilities still fail closed.
- There is no GraphQL multipart upload, resumable upload, S3 multipart upload,
  deduplication, asset library, transformation pipeline, or built-in virus
  scanner.
- Existing stored file names require no data migration, but their unavailable
  metadata stays `null` and exact retained-version guarantees begin with files
  uploaded through this workflow.

See [the local-storage guide](../../howto/graphql_file_uploads.md) and
[the S3 guide](../../howto/graphql_file_uploads_s3.md) for complete setup.
