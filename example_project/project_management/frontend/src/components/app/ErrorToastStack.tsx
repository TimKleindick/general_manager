import { useEffect, useMemo, useState } from "react";
import { useAppSelector } from "@/store";

type ToastItem = {
  id: string;
  message: string;
};

const TOAST_TTL_MS = 4500;

export function ErrorToastStack() {
  const notifications = useAppSelector((s) => s.app.notifications);
  const [toasts, setToasts] = useState<ToastItem[]>([]);

  const errorNotifications = useMemo(
    () => notifications.filter((item) => item.event === "error"),
    [notifications]
  );

  useEffect(() => {
    setToasts((prev) => {
      const existing = new Set(prev.map((item) => item.id));
      const next = [...prev];
      for (const item of errorNotifications) {
        if (existing.has(item.id)) continue;
        next.unshift({
          id: item.id,
          message: item.message || `${item.entityType} request failed`,
        });
      }
      return next.slice(0, 4);
    });
  }, [errorNotifications]);

  useEffect(() => {
    if (!toasts.length) return;
    const timers = toasts.map((item) =>
      window.setTimeout(() => {
        setToasts((prev) => prev.filter((entry) => entry.id !== item.id));
      }, TOAST_TTL_MS)
    );
    return () => timers.forEach((timer) => window.clearTimeout(timer));
  }, [toasts]);

  if (!toasts.length) return null;

  return (
    <div className="fixed right-3 top-16 z-40 grid w-[min(28rem,92vw)] gap-2">
      {toasts.map((toast) => (
        <article
          key={toast.id}
          className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-900 shadow-lg"
          role="status"
          aria-live="polite"
        >
          <p className="font-semibold">Request error</p>
          <p className="mt-1">{toast.message}</p>
        </article>
      ))}
    </div>
  );
}
