const AI_GATEWAY_URL = "/ai/query";
const AI_CHAT_URL = "/ai/chat";

type GatewayError = {
  code: string;
  message: string;
};

type GatewayResponse = {
  data: {
    rows?: Array<Record<string, unknown>>;
    aggregates?: Record<string, number | null>;
    page_info?: Record<string, unknown>;
    domains?: Array<Record<string, unknown>>;
    schema?: Record<string, unknown>;
    plan?: Record<string, unknown>;
  };
  provenance: Record<string, unknown>;
  errors: GatewayError[];
  follow_up_suggestions?: string[];
};

function getCookie(name: string): string | null {
  const value = document.cookie
    .split("; ")
    .find((row) => row.startsWith(`${name}=`))
    ?.split("=")[1];
  return value ? decodeURIComponent(value) : null;
}

export async function executeAiQuery(payload: Record<string, unknown>): Promise<GatewayResponse> {
  const csrfToken = getCookie("csrftoken");
  const response = await fetch(AI_GATEWAY_URL, {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      ...(csrfToken ? { "X-CSRFToken": csrfToken } : {}),
    },
    body: JSON.stringify(payload),
  });

  const data = (await response.json()) as GatewayResponse;
  if (!response.ok && !data.errors?.length) {
    throw new Error(`AI gateway request failed (${response.status})`);
  }
  return data;
}

export async function executeAiChat(question: string): Promise<{
  answer: string;
  question: string;
  query_request?: Record<string, unknown>;
  gateway_response?: GatewayResponse;
  errors?: GatewayError[];
}> {
  const csrfToken = getCookie("csrftoken");
  const response = await fetch(AI_CHAT_URL, {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      ...(csrfToken ? { "X-CSRFToken": csrfToken } : {}),
    },
    body: JSON.stringify({ question }),
  });
  const data = (await response.json()) as {
    answer: string;
    question: string;
    query_request?: Record<string, unknown>;
    gateway_response?: GatewayResponse;
    errors?: GatewayError[];
  };
  if (!response.ok && !data.errors?.length) {
    throw new Error(`AI chat request failed (${response.status})`);
  }
  return data;
}

export type { GatewayResponse, GatewayError };
