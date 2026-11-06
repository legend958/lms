import {
  cloneElement,
  isValidElement,
  useId,
  type InputHTMLAttributes,
  type LabelHTMLAttributes,
  type ReactElement,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";

import { cn } from "@/lib/utils";

const field =
  "w-full rounded-lg border border-ink-200 bg-white px-3 py-2 text-sm text-ink-900 placeholder:text-ink-500 shadow-sm transition focus:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-100 disabled:bg-ink-50 disabled:text-ink-500";

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cn(field, "h-10", className)} {...props} />;
}

export function Textarea({
  className,
  ...props
}: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cn(field, "min-h-28", className)} {...props} />;
}

export function Select({
  className,
  ...props
}: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={cn(field, "h-10 pr-8", className)} {...props} />;
}

export function Label({ className, ...props }: LabelHTMLAttributes<HTMLLabelElement>) {
  return (
    <label
      className={cn("mb-1.5 block text-xs font-semibold uppercase tracking-wide text-ink-500", className)}
      {...props}
    />
  );
}

export function Field({
  label,
  hint,
  htmlFor,
  children,
}: {
  label: string;
  hint?: string;
  htmlFor?: string;
  children: ReactNode;
}) {
  const generatedId = useId();
  const id = htmlFor ?? generatedId;
  const hintId = hint ? `${id}-hint` : undefined;

  const control = isValidElement(children)
    ? cloneElement(children as ReactElement<Record<string, unknown>>, {
        id: (children.props as { id?: string }).id ?? id,
        "aria-describedby": hintId,
      })
    : children;

  return (
    <div>
      <Label htmlFor={id}>{label}</Label>
      {control}
      {hint ? (
        <p id={hintId} className="mt-1 text-xs text-ink-500">
          {hint}
        </p>
      ) : null}
    </div>
  );
}
