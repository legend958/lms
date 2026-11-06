import Link from "next/link";

export default function NotFound() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-ink-50 px-6">
      <div className="w-full max-w-md rounded-2xl border border-ink-200 bg-white p-8 text-center shadow-sm">
        <p className="text-xs font-semibold uppercase tracking-wide text-ink-500">404</p>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight text-ink-950">
          Page not found
        </h1>
        <p className="mt-2 text-sm text-ink-600">
          The page you are looking for does not exist, or it belongs to a different portal.
        </p>
        <Link
          href="/"
          className="mt-6 inline-flex h-10 items-center justify-center rounded-lg bg-indigo-600 px-4 text-sm font-medium text-white transition-colors hover:bg-indigo-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600"
        >
          Go to your workspace
        </Link>
      </div>
    </main>
  );
}
