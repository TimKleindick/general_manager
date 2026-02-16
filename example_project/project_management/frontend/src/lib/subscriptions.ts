const GRAPHQL_WS_PATH = "/graphql/";

type Status = "connecting" | "connected" | "disconnected" | "error";

type SubscribeConfig = {
  query: string;
  variables?: Record<string, unknown>;
  operationName?: string;
  onNext?: (data: Record<string, unknown> | null) => void;
  onError?: (errors: unknown) => void;
  onComplete?: () => void;
};

type SubscriptionOptions = {
  onStatus?: (status: Status) => void;
  reconnectBaseMs?: number;
  reconnectMaxMs?: number;
};

function wsUrl() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}${GRAPHQL_WS_PATH}`;
}

export function createSubscriptionClient(options: SubscriptionOptions = {}) {
  const reconnectBaseMs = options.reconnectBaseMs ?? 500;
  const reconnectMaxMs = options.reconnectMaxMs ?? 8000;
  const onStatus = options.onStatus ?? (() => undefined);

  let socket: WebSocket | null = null;
  let acked = false;
  let manuallyClosed = false;
  let operationCounter = 0;
  let reconnectAttempt = 0;
  let reconnectTimer: number | null = null;

  const operations = new Map<
    string,
    SubscribeConfig & {
      sent: boolean;
    }
  >();

  function sendMessage(message: Record<string, unknown>) {
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    socket.send(JSON.stringify(message));
  }

  function flushSubscriptions() {
    if (!acked) return;
    for (const [operationId, operation] of operations.entries()) {
      if (operation.sent) continue;
      sendMessage({
        id: operationId,
        type: "subscribe",
        payload: {
          query: operation.query,
          variables: operation.variables ?? {},
          operationName: operation.operationName ?? undefined,
        },
      });
      operation.sent = true;
    }
  }

  function connect() {
    if (
      socket &&
      (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)
    ) {
      return;
    }

    socket = new WebSocket(wsUrl(), "graphql-transport-ws");
    acked = false;
    onStatus("connecting");

    socket.addEventListener("open", () => {
      reconnectAttempt = 0;
      sendMessage({ type: "connection_init" });
    });

    socket.addEventListener("message", (event) => {
      let message: Record<string, unknown>;
      try {
        message = JSON.parse(event.data) as Record<string, unknown>;
      } catch {
        return;
      }
      const type = message.type;

      if (type === "connection_ack") {
        acked = true;
        onStatus("connected");
        flushSubscriptions();
        return;
      }

      if (type === "ping") {
        sendMessage({ type: "pong", payload: message.payload });
        return;
      }

      const id = String(message.id || "");
      if (!id) return;
      const operation = operations.get(id);
      if (!operation) return;

      if (type === "next") {
        const payload = (message.payload as { data?: Record<string, unknown> }) || null;
        operation.onNext?.(payload?.data || null);
      } else if (type === "error") {
        operation.onError?.(message.payload);
      } else if (type === "complete") {
        operation.onComplete?.();
      }
    });

    socket.addEventListener("close", () => {
      acked = false;
      onStatus("disconnected");
      for (const operation of operations.values()) operation.sent = false;
      if (manuallyClosed) return;
      reconnectAttempt += 1;
      const delay = Math.min(reconnectBaseMs * 2 ** reconnectAttempt, reconnectMaxMs);
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      reconnectTimer = window.setTimeout(connect, delay);
    });

    socket.addEventListener("error", () => {
      onStatus("error");
    });
  }

  function subscribe(config: SubscribeConfig) {
    const operationId = String(++operationCounter);
    operations.set(operationId, { ...config, sent: false });
    connect();
    flushSubscriptions();
    return {
      id: operationId,
      unsubscribe() {
        const operation = operations.get(operationId);
        if (!operation) return;
        operations.delete(operationId);
        sendMessage({ id: operationId, type: "complete" });
      },
    };
  }

  function close() {
    manuallyClosed = true;
    if (reconnectTimer) window.clearTimeout(reconnectTimer);
    for (const operationId of operations.keys()) {
      sendMessage({ id: operationId, type: "complete" });
    }
    operations.clear();
    socket?.close();
    socket = null;
  }

  return { subscribe, connect, close };
}
