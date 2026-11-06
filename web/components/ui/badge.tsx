import type { HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

type Tone = "neutral" | "success" | "warning" | "danger" | "info" | "indigo";

const tones: Record<Tone, string> = {
  neutral: "bg-ink-100 text-ink-700",
  success: "bg-emerald-50 text-emerald-700 ring-1 ring-inset ring-emerald-200",
  warning: "bg-amber-50 text-amber-700 ring-1 ring-inset ring-amber-200",
  danger: "bg-rose-50 text-rose-700 ring-1 ring-inset ring-rose-200",
  info: "bg-sky-50 text-sky-700 ring-1 ring-inset ring-sky-200",
  indigo: "bg-indigo-50 text-indigo-700 ring-1 ring-inset ring-indigo-200",
};

export function Badge({
  tone = "neutral",
  className,
  ...props
}: HTMLAttributes<HTMLSpanElement> & { tone?: Tone }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold capitalize",
        tones[tone],
        className,
      )}
      {...props}
    />
  );
}
