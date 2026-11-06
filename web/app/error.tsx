"use client";

import { useEffect } from "react";

export default function ErrorPage({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("route_error", error);
  }, [error]);

  return (
    <main className="flex min-h-screen items-center justify-center bg-ink-50 px-6">
      <div className="w-full max-w-md rounded-2xl border border-ink-200 bg-white p-8 text-center shadow-sm">
        <p className="text-xs font-semibold uppercase tracking-wide text-rose-600">Error</p>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight text-ink-950">
          Something went wrong
        </h1>
        <p className="mt-2 text-sm text-ink-600">
          The page could not be rendered. Try again, and if the problem persists check the
          service health.
        </p>
        {error.digest ? (
          <p className="mt-2 font-mono text-xs text-ink-500">Reference: {error.digest}</p>
        ) : null}
        <button
          type="button"
          onClick={reset}
          className="mt-6 inline-flex h-10 items-center justify-center rounded-lg bg-indigo-600 px-4 text-sm font-medium text-white transition-colors hover:bg-indigo-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600"
        >
          Try again
        </button>
      </div>
    </main>
  );
}
