# Upload a file through GraphQL

This browser-side recipe assumes the server is configured with the
[local upload guide](../howto/graphql_file_uploads.md). It calculates the exact
SHA-256 expected by `beginFileUpload`, performs the returned transfer, and then
consumes the one-time token in a generated update mutation.

```javascript
async function sha256Hex(file) {
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
  if (payload.errors) throw new Error(payload.errors[0].message);
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
  if (!transfer.ok) throw new Error(`upload failed: ${transfer.status}`);

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

See the [concept page](../concepts/graphql/file_uploads.md), the
[S3 task guide](../howto/graphql_file_uploads_s3.md), and the
[API reference](../api/graphql.md#file-uploads).
