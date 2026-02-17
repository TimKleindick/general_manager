import { store } from "@/store";
import { pushNotification } from "@/store/slices/appSlice";

const GRAPHQL_HTTP_URL = "/graphql/";

type GraphQLErrorItem = {
  message?: string;
};

type GraphQLResponse<T> = {
  data?: T;
  errors?: GraphQLErrorItem[];
};

function reportRequestError(message: string) {
  store.dispatch(
    pushNotification({
      event: "error",
      entityType: "Request",
      entityId: "-",
      message,
    })
  );
}

function getCookie(name: string): string | null {
  const value = document.cookie
    .split("; ")
    .find((row) => row.startsWith(`${name}=`))
    ?.split("=")[1];
  return value ? decodeURIComponent(value) : null;
}

export async function executeQuery<T>(query: string, variables: Record<string, unknown>) {
  const csrfToken = getCookie("csrftoken");
  try {
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
    if (!response.ok) {
      throw new Error(
        payload.errors?.[0]?.message || `Request failed (${response.status})`
      );
    }
    if (payload.errors && payload.errors.length) {
      throw new Error(payload.errors[0]?.message || "GraphQL query failed.");
    }
    return payload.data as T;
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Network request failed.";
    reportRequestError(message);
    throw error;
  }
}

export async function executeMutation<T>(mutation: string, variables: Record<string, unknown>) {
  return executeQuery<T>(mutation, variables);
}
