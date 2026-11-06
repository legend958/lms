"use client";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="en">
      <body>
        <main style={{ fontFamily: "system-ui, sans-serif", padding: "3rem", textAlign: "center" }}>
          <h1 style={{ fontSize: "1.5rem", fontWeight: 600 }}>Application error</h1>
          <p style={{ marginTop: "0.5rem", color: "#4f5d73" }}>
            The application could not start. Please retry.
          </p>
          {error.digest ? (
            <p style={{ marginTop: "0.5rem", fontFamily: "monospace", fontSize: "0.75rem" }}>
              Reference: {error.digest}
            </p>
          ) : null}
          <button
            type="button"
            onClick={reset}
            style={{ marginTop: "1.5rem", padding: "0.6rem 1rem", borderRadius: "0.5rem" }}
          >
            Try again
          </button>
        </main>
      </body>
    </html>
  );
}
