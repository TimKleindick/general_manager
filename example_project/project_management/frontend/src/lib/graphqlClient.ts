const GRAPHQL_HTTP_URL = "/graphql/";

type GraphQLErrorItem = {
  message?: string;
};

type GraphQLResponse<T> = {
  data?: T;
  errors?: GraphQLErrorItem[];
};

function getCookie(name: string): string | null {
  const value = document.cookie
    .split("; ")
    .find((row) => row.startsWith(`${name}=`))
    ?.split("=")[1];
  return value ? decodeURIComponent(value) : null;
}

export async function executeQuery<T>(query: string, variables: Record<string, unknown>) {
  const csrfToken = getCookie("csrftoken");
  const response = await fetch(GRAPHQL_HTTP_URL, {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      ...(csrfToken ? { "X-CSRFToken": csrfToken } : {}),
    },
    body: JSON.stringify({ query, variables }),
  });
  const payload = (await response.json()) as GraphQLResponse<T>;
  if (payload.errors && payload.errors.length) {
    throw new Error(payload.errors[0]?.message || "GraphQL query failed.");
  }
  return payload.data as T;
}

export async function executeMutation<T>(mutation: string, variables: Record<string, unknown>) {
  return executeQuery<T>(mutation, variables);
}
