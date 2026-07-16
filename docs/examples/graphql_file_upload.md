# Upload a file through GraphQL

This browser-side recipe assumes the server is configured with the
[local upload guide](../howto/graphql_file_uploads.md). It calculates the exact
SHA-256 expected by `beginFileUpload`, performs the returned transfer, and then
consumes the one-time token in a generated update mutation.

```javascript
import {
  GraphQLRequestError,
  mapGraphQLErrors,
} from "./graphql-errors.js";

// Web Crypto does not expose incremental SHA-256. Keep this limit no higher
// than the server's upload policy, or use a reviewed streaming hash library.
const MAX_CLIENT_HASH_BYTES = 25_000_000;

async function sha256Hex(file) {
  if (file.size > MAX_CLIENT_HASH_BYTES) {
    throw new RangeError("The file is too large to hash in this browser flow.");
  }
  const bytes = await file.arrayBuffer();
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

async function graphql(query, variables) {
  const response = await fetch("/graphql", {
    method: "POST",
    credentials: "same-origin",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({query, variables}),
  });
  const payload = await response.json();
  const failures = mapGraphQLErrors(payload.errors ?? [], {
    isPublicCode: (code) => (
      code === "UNAUTHENTICATED"
      || code === "INVALID_FILE_TYPE"
      || code === "INVALID_IMAGE"
      || code.startsWith("UPLOAD_")
      || code.startsWith("INVALID_UPLOAD_")
    ),
  });
  if (failures.length) throw new GraphQLRequestError(failures);
  return payload.data;
}

async function uploadAvatar(profileId, file) {
  const begin = await graphql(`
    mutation Begin($id: ID!, $name: String!, $size: BigIntScalar!,
                   $type: String!, $digest: String!) {
      beginFileUpload(
        manager: "Profile", field: "avatar", operation: UPDATE,
        objectId: $id, filename: $name, size: $size,
        contentType: $type,
        checksum: {algorithm: SHA256, digest: $digest}
      ) {
        token transport uploadUrl method
        headers { name value }
      }
    }
  `, {
    id: String(profileId),
    name: file.name,
    size: String(file.size),
    type: file.type || "application/octet-stream",
    digest: await sha256Hex(file),
  });

  const instructions = begin.beginFileUpload;
  const headers = Object.fromEntries(
    instructions.headers
      .filter(({name}) => name.toLowerCase() !== "content-length")
      .map(({name, value}) => [name, value]),
  );
  const transfer = await fetch(instructions.uploadUrl, {
    method: instructions.method,
    credentials: instructions.transport === "PROXY" ? "same-origin" : "omit",
    headers,
    body: file,
  });
  if (!transfer.ok) {
    const payload = await transfer.json().catch(() => null);
    const code = typeof payload?.error?.code === "string"
      ? payload.error.code
      : "UPLOAD_FAILED";
    throw new GraphQLRequestError([
      {code, message: "The upload transfer failed."},
    ]);
  }

  return graphql(`
    mutation Finish($id: Int!, $token: UploadToken!) {
      updateProfile(id: $id, avatar: $token) {
        Profile { avatar { name status downloadUrl expiresAt } }
      }
    }
  `, {id: profileId, token: instructions.token});
}
```

Do not retry the final mutation with the same token once the intent reaches
`FINALIZING`. Poll the normal profile query while the returned status is
`PROCESSING`; begin a new upload after a terminal token error. This workflow and
its stable Python extension contracts were introduced in 0.62.0.

The 25 MB browser hashing limit matches GeneralManager's default `MAX_BYTES`.
Lower it when the field policy is smaller. For larger configured uploads, use a
reviewed incremental SHA-256 implementation instead of increasing the buffered
limit without considering client memory.

See the [concept page](../concepts/graphql/file_uploads.md), the
[S3 task guide](../howto/graphql_file_uploads_s3.md), and the
[API reference](../api/graphql.md#file-uploads).
