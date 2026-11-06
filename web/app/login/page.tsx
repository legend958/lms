"use client";

import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/input";
import { ApiError, apiFetch } from "@/lib/api";
import { homeForRole } from "@/lib/utils";
import type { User } from "@/lib/types";

const DEMO_ACCOUNTS = [
  { label: "Admin", email: "admin@lms.example.com", password: "ChangeMe123!" },
  { label: "Teacher", email: "teacher1@lms.example.com", password: "Passw0rd!" },
  { label: "Student", email: "student01@lms.example.com", password: "Passw0rd!" },
];

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const response = await apiFetch<{ user: User }>("/api/v1/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      window.location.assign(homeForRole(response.user.role));
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.status === 429 ? "Too many attempts. Wait a minute and try again." : err.message);
      } else {
        setError("Unable to reach the server. Is the API running?");
      }
      setSubmitting(false);
    }
  }

  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      <section className="relative hidden overflow-hidden bg-ink-950 p-12 lg:flex lg:flex-col lg:justify-between">
        <div className="absolute -right-24 -top-24 h-96 w-96 rounded-full bg-indigo-500/20 blur-3xl" />
        <div className="absolute -bottom-24 -left-24 h-96 w-96 rounded-full bg-violet-500/10 blur-3xl" />

        <div className="relative">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-400 to-violet-500 text-sm font-bold text-white shadow-lg shadow-indigo-900/40">
              L
            </div>
            <div>
              <p className="text-sm font-semibold text-white">LMS Console</p>
              <p className="text-xs text-ink-500">RAG learning platform</p>
            </div>
          </div>

          <h1 className="mt-16 max-w-md text-4xl font-semibold leading-tight tracking-tight text-white">
            Course material that answers back.
          </h1>
          <p className="mt-4 max-w-md text-sm leading-relaxed text-ink-300">
            Upload a PDF, let the platform index it, and give every student a grounded
            assistant that only sees the topics their teacher has released.
          </p>

          <ul className="mt-10 space-y-4 text-sm text-ink-300">
            {[
              "Three portals: student, teacher, admin",
              "Release-gated retrieval by topic",
              "Citations back to the exact page",
              "Quizzes and progress in one place",
            ].map((item) => (
              <li key={item} className="flex items-center gap-3">
                <span className="flex h-5 w-5 items-center justify-center rounded-full bg-indigo-500/20 text-[10px] text-indigo-300">
                  ✓
                </span>
                {item}
              </li>
            ))}
          </ul>
        </div>

        <p className="relative text-xs text-ink-500">
          Phases 1–3 — identity, courses, PDF ingestion, and release-gated topics.
        </p>
      </section>

      <section className="flex items-center justify-center px-4 py-12 sm:px-8">
        <div className="w-full max-w-sm">
          <div className="mb-8 lg:hidden">
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-400 to-violet-500 text-sm font-bold text-white">
                L
              </div>
              <div>
                <p className="text-sm font-semibold text-ink-900">LMS Console</p>
                <p className="text-xs text-ink-500">RAG learning platform</p>
              </div>
            </div>
          </div>

          <h2 className="text-2xl font-semibold tracking-tight text-ink-950">Sign in</h2>
          <p className="mt-1 text-sm text-ink-500">Use the account your administrator created.</p>

          <form onSubmit={onSubmit} className="mt-8 space-y-4">
            <div>
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                placeholder="you@school.edu"
              />
            </div>
            <div>
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder="••••••••"
              />
            </div>

            {error ? (
              <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
                {error}
              </div>
            ) : null}

            <Button type="submit" className="w-full" disabled={submitting}>
              {submitting ? "Signing in…" : "Sign in"}
            </Button>
          </form>

          <div className="mt-8 rounded-xl border border-ink-200 bg-white p-4">
            <p className="text-xs font-semibold uppercase tracking-wide text-ink-500">
              Demo accounts
            </p>
            <div className="mt-3 space-y-2">
              {DEMO_ACCOUNTS.map((account) => (
                <button
                  key={account.email}
                  type="button"
                  onClick={() => {
                    setEmail(account.email);
                    setPassword(account.password);
                    setError(null);
                  }}
                  className="flex w-full items-center justify-between rounded-lg px-2 py-1.5 text-left text-xs transition-colors hover:bg-ink-50"
                >
                  <span className="font-medium text-ink-700">{account.label}</span>
                  <span className="font-mono text-ink-500">{account.email}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
