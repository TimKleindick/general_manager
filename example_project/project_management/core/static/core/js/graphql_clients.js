(function () {
  "use strict";

  const GRAPHQL_HTTP_URL = "/graphql/";
  const GRAPHQL_WS_PATH = "/graphql/";

  async function executeQuery(query, variables) {
    const response = await fetch(GRAPHQL_HTTP_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, variables: variables || {} }),
    });
    const payload = await response.json();
    if (payload.errors && payload.errors.length) {
      const first = payload.errors[0] || {};
      throw new Error(first.message || "GraphQL query failed.");
    }
    return payload.data;
  }

  async function executeMutation(mutation, variables) {
    return executeQuery(mutation, variables);
  }

  function createEventBus() {
    const listeners = new Map();
    return {
      on(eventName, listener) {
        if (!listeners.has(eventName)) {
          listeners.set(eventName, new Set());
        }
        listeners.get(eventName).add(listener);
        return () => {
          listeners.get(eventName)?.delete(listener);
        };
      },
      emit(eventName, payload) {
        const registered = listeners.get(eventName);
        if (!registered) return;
        for (const listener of registered) {
          try {
            listener(payload);
          } catch (_err) {
            // Intentionally isolated from other listeners.
          }
        }
      },
    };
  }

  function wsUrl() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${protocol}//${window.location.host}${GRAPHQL_WS_PATH}`;
  }

  function createSubscriptionClient(options = {}) {
    const reconnectBaseMs = options.reconnectBaseMs || 500;
    const reconnectMaxMs = options.reconnectMaxMs || 8000;
    const onStatus = options.onStatus || function () {};

    let socket = null;
    let acked = false;
    let manuallyClosed = false;
    let operationCounter = 0;
    let reconnectAttempt = 0;
    let reconnectTimer = null;

    const operations = new Map();

    function sendMessage(message) {
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
            variables: operation.variables || {},
            operationName: operation.operationName || undefined,
          },
        });
        operation.sent = true;
      }
    }

    function connect() {
      if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
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
        let message;
        try {
          message = JSON.parse(event.data);
        } catch (_err) {
          return;
        }

        if (message.type === "connection_ack") {
          acked = true;
          onStatus("connected");
          flushSubscriptions();
          return;
        }

        if (message.type === "ping") {
          sendMessage({ type: "pong", payload: message.payload || undefined });
          return;
        }

        if (!message.id) return;
        const operation = operations.get(message.id);
        if (!operation) return;

        if (message.type === "next") {
          operation.onNext?.(message.payload?.data || null);
          return;
        }
        if (message.type === "error") {
          operation.onError?.(message.payload || [{ message: "Subscription error." }]);
          return;
        }
        if (message.type === "complete") {
          operation.onComplete?.();
        }
      });

      socket.addEventListener("close", () => {
        acked = false;
        onStatus("disconnected");
        for (const operation of operations.values()) {
          operation.sent = false;
        }
        if (manuallyClosed) return;
        reconnectAttempt += 1;
        const delay = Math.min(reconnectBaseMs * 2 ** reconnectAttempt, reconnectMaxMs);
        clearTimeout(reconnectTimer);
        reconnectTimer = setTimeout(() => {
          connect();
        }, delay);
      });

      socket.addEventListener("error", () => {
        onStatus("error");
      });
    }

    function subscribe(config) {
      const operationId = String(++operationCounter);
      operations.set(operationId, {
        query: config.query,
        variables: config.variables || {},
        operationName: config.operationName || null,
        onNext: config.onNext,
        onError: config.onError,
        onComplete: config.onComplete,
        sent: false,
      });
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
      clearTimeout(reconnectTimer);
      for (const operationId of operations.keys()) {
        sendMessage({ id: operationId, type: "complete" });
      }
      operations.clear();
      if (socket) socket.close();
      socket = null;
    }

    return { subscribe, close, connect };
  }

  window.GMGraphQLClient = {
    executeQuery,
    executeMutation,
    createSubscriptionClient,
    createEventBus,
  };
})();
