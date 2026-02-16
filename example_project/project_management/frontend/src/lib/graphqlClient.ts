const GRAPHQL_HTTP_URL = "/graphql/";

type GraphQLErrorItem = {
  message?: string;
};

type GraphQLResponse<T> = {
  data?: T;
  errors?: GraphQLErrorItem[];
};

export async function executeQuery<T>(query: string, variables: Record<string, unknown>) {
  const response = await fetch(GRAPHQL_HTTP_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
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
