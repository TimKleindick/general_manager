import { useEffect } from "react";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

type Props = {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  className?: string;
  headerActions?: ReactNode;
};

export function Dialog({ open, onClose, title, children, className, headerActions }: Props) {
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center p-3">
      <button className="absolute inset-0 bg-black/45" onClick={onClose} aria-label="Close dialog" />
      <section className={cn("relative z-50 max-h-[95vh] w-full max-w-4xl overflow-auto rounded-xl border border-border bg-card shadow-xl", className)}>
        <header className="sticky top-0 z-10 flex items-center justify-between border-b border-border bg-card p-3">
          <h3 className="font-serif text-lg font-semibold">{title}</h3>
          <div className="flex items-center gap-2">
            {headerActions}
            <button className="rounded border border-border px-3 py-1 text-sm" onClick={onClose}>Close</button>
          </div>
        </header>
        <div className="p-3">{children}</div>
      </section>
    </div>
  );
}
