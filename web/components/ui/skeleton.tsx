import { cn } from "@/lib/utils";

export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={cn("animate-pulse rounded-lg bg-ink-200/70", className)}
    />
  );
}

export function PageSkeleton({ label = "Loading page" }: { label?: string }) {
  return (
    <div role="status" aria-live="polite" aria-label={label} className="space-y-6">
      <div className="space-y-2">
        <Skeleton className="h-7 w-56" />
        <Skeleton className="h-4 w-80 max-w-full" />
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        <Skeleton className="h-24" />
        <Skeleton className="h-24" />
        <Skeleton className="h-24" />
      </div>
      <Skeleton className="h-64" />
      <span className="sr-only">{label}</span>
    </div>
  );
}
