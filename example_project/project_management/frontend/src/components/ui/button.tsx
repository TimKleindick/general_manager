import type { ButtonHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

type Variant = "default" | "outline" | "ghost" | "destructive";

type Props = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
};

const variants: Record<Variant, string> = {
  default: "bg-primary text-primary-foreground border-primary hover:opacity-90",
  outline: "bg-white/90 border-border hover:bg-muted",
  ghost: "bg-transparent border-transparent hover:bg-muted",
  destructive: "bg-destructive text-destructive-foreground border-destructive hover:opacity-90",
};

export function Button({ className, variant = "outline", ...props }: Props) {
  return (
    <button
      className={cn(
        "inline-flex items-center justify-center rounded-md border px-3 py-2 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-60",
        variants[variant],
        className
      )}
      {...props}
    />
  );
}
