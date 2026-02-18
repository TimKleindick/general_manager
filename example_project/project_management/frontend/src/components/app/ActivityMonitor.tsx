import { useEffect, useMemo, useState } from "react";
import { Bell } from "lucide-react";
import { useAppDispatch, useAppSelector } from "@/store";
import { clearNotifications } from "@/store/slices/appSlice";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { AiChatModal } from "@/components/app/AiChatModal";

export function ActivityMonitor() {
  const dispatch = useAppDispatch();
  const notifications = useAppSelector((s) => s.app.notifications);
  const wsStatus = useAppSelector((s) => s.app.wsStatus);

  const [open, setOpen] = useState(false);
  const [seenCount, setSeenCount] = useState(0);

  useEffect(() => {
    if (!open) return;
    setSeenCount(notifications.length);
  }, [open, notifications.length]);

  const unreadCount = useMemo(() => Math.max(0, notifications.length - seenCount), [notifications.length, seenCount]);

  return (
    <>
      <div className="fixed right-3 top-3 z-30 flex items-center gap-2">
        <AiChatModal />
        <Button
          className="relative gap-2 bg-white/95 shadow-lg"
          variant="outline"
          onClick={() => setOpen(true)}
          aria-label="Open activity monitor"
        >
          <Bell className="h-4 w-4" />
          Activity
          {unreadCount > 0 ? (
            <span className="ml-1 rounded-full bg-red-600 px-2 py-0.5 text-xs font-semibold text-white">
              new {unreadCount}
            </span>
          ) : null}
        </Button>
      </div>

      <Dialog open={open} onClose={() => setOpen(false)} title="Activity Monitor" className="max-w-3xl">
        <div className="mb-3 flex items-center justify-between text-sm text-muted-foreground">
          <p>WebSocket status: {wsStatus}</p>
          <Button variant="outline" onClick={() => dispatch(clearNotifications())}>
            Clear
          </Button>
        </div>
        <div className="grid max-h-[60vh] gap-2 overflow-auto text-sm">
          {notifications.length ? (
            notifications.map((item, idx) => (
              <article key={`${item.id}-${idx}`} className="rounded-md border border-border bg-white p-2">
                <p className="font-medium">
                  {item.event} {item.entityType} #{item.entityId}
                </p>
                {item.message ? <p className="text-muted-foreground">{item.message}</p> : null}
                <p className="text-xs text-muted-foreground">{new Date(item.timestamp).toLocaleString()}</p>
              </article>
            ))
          ) : (
            <p className="text-muted-foreground">No activity yet.</p>
          )}
        </div>
      </Dialog>
    </>
  );
}
