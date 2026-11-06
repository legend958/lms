"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { cn } from "@/lib/utils";

export type ToastTone = "success" | "error" | "info";

export interface Toast {
  id: number;
  title: string;
  description?: string;
  tone: ToastTone;
}

interface ToastContextValue {
  toast: (input: { title: string; description?: string; tone?: ToastTone }) => void;
  dismiss: (id: number) => void;
  toasts: Toast[];
}

let nextToastId = 1;

const TONE: Record<ToastTone, string> = {
  success: "border-emerald-200 bg-white text-emerald-900",
  error: "border-rose-200 bg-white text-rose-900",
  info: "border-ink-200 bg-white text-ink-900",
};

const DOT: Record<ToastTone, string> = {
  success: "bg-emerald-500",
  error: "bg-rose-500",
  info: "bg-indigo-500",
};

// The context lives in its own module so every portal can import the hook
// without pulling the provider into a server component.
const ToastContext = createContext<ToastContextValue | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const timers = useRef(new Map<number, ReturnType<typeof setTimeout>>());

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((item) => item.id !== id));
    const timer = timers.current.get(id);
    if (timer) {
      clearTimeout(timer);
      timers.current.delete(id);
    }
  }, []);

  const toast = useCallback(
    ({
      title,
      description,
      tone = "info",
    }: {
      title: string;
      description?: string;
      tone?: ToastTone;
    }) => {
      const id = nextToastId++;
      setToasts((current) => [...current.slice(-3), { id, title, description, tone }]);
      const timer = setTimeout(() => dismiss(id), 6000);
      timers.current.set(id, timer);
    },
    [dismiss],
  );

  useEffect(() => {
    const active = timers.current;
    return () => {
      active.forEach((timer) => clearTimeout(timer));
      active.clear();
    };
  }, []);

  const value = useMemo(() => ({ toast, dismiss, toasts }), [toast, dismiss, toasts]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div
        aria-live="polite"
        aria-atomic="false"
        className="pointer-events-none fixed inset-x-0 bottom-4 z-[60] flex flex-col items-center gap-2 px-4 sm:items-end sm:pr-6"
      >
        {toasts.map((item) => (
          <div
            key={item.id}
            role="status"
            className={cn(
              "pointer-events-auto flex w-full max-w-sm items-start gap-3 rounded-xl border px-4 py-3 shadow-lg shadow-ink-950/10",
              TONE[item.tone],
            )}
          >
            <span className={cn("mt-1.5 h-2 w-2 shrink-0 rounded-full", DOT[item.tone])} />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold">{item.title}</p>
              {item.description ? (
                <p className="mt-0.5 text-xs text-ink-600">{item.description}</p>
              ) : null}
            </div>
            <button
              type="button"
              onClick={() => dismiss(item.id)}
              className="rounded p-1 text-ink-500 transition hover:bg-ink-100 hover:text-ink-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-500"
              aria-label="Dismiss notification"
            >
              <span aria-hidden="true">×</span>
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const context = useContext(ToastContext);
  if (context === null) {
    throw new Error("useToast must be used inside a ToastProvider");
  }
  return context;
}
