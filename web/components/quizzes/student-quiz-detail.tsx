"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorBanner, Spinner } from "@/components/ui/feedback";
import { ApiError, apiFetch } from "@/lib/api";
import { useStudentQuiz } from "@/lib/hooks";
import type { StudentAttemptState } from "@/lib/types";

export function StudentQuizDetail({ quizId }: { quizId: string }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { data, isLoading, error } = useStudentQuiz(quizId);
  const [actionError, setActionError] = useState<string | null>(null);

  const start = useMutation({
    mutationFn: () =>
      apiFetch<StudentAttemptState>(`/api/v1/student/quizzes/${quizId}/attempts`, {
        method: "POST",
      }),
    onSuccess: (state) => {
      void queryClient.invalidateQueries({ queryKey: ["student-quizzes"] });
      void queryClient.invalidateQueries({ queryKey: ["student-attempts"] });
      router.push(`/student/tests/attempts/${state.attempt_id}`);
    },
    onError: (err) =>
      setActionError(
        err instanceof ApiError ? err.message : "Could not start this test. Try again.",
      ),
  });

  if (isLoading) return <Spinner label="Loading test" />;
  if (error) return <ErrorBanner message="Could not load this test." />;
  if (!data) return <ErrorBanner message="Test not found." />;

  const quiz = data.quiz;
  const limitReached = quiz.attempt_limit > 0 && quiz.attempts_used >= quiz.attempt_limit;

  return (
    <div>
      <div className="mb-6">
        <Link
          href="/student/tests"
          className="text-xs font-semibold uppercase tracking-wide text-indigo-600 hover:text-indigo-700"
        >
          Back to tests
        </Link>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <h1 className="text-2xl font-semibold tracking-tight text-ink-950">{quiz.title}</h1>
          {quiz.best_score !== null ? (
            <Badge tone={quiz.best_score >= quiz.pass_threshold ? "success" : "danger"}>
              Best {quiz.best_score}%
            </Badge>
          ) : null}
        </div>
        <p className="mt-1 text-sm text-ink-500">
          {quiz.course.code} · {quiz.course.title}
        </p>
      </div>

      {actionError ? (
        <div className="mb-4">
          <ErrorBanner message={actionError} />
        </div>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Before you start</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-3">
              {[
                { label: "Questions", value: String(quiz.question_count) },
                { label: "Pass mark", value: `${quiz.pass_threshold}%` },
                {
                  label: "Attempts used",
                  value:
                    quiz.attempt_limit === 0
                      ? `${quiz.attempts_used}`
                      : `${quiz.attempts_used} / ${quiz.attempt_limit}`,
                },
              ].map((item) => (
                <div key={item.label} className="rounded-lg bg-ink-50 px-3 py-3">
                  <p className="text-xs font-semibold uppercase tracking-wide text-ink-500">
                    {item.label}
                  </p>
                  <p className="mt-1 text-lg font-semibold text-ink-900">{item.value}</p>
                </div>
              ))}
            </div>

            <ul className="space-y-2 text-sm text-ink-600">
              <li>· One correct option per question. Selecting an answer autosaves it.</li>
              <li>· Grading happens on the server when you submit; you cannot change answers after.</li>
              <li>· After submission you see the correct answers, explanations, and source excerpts.</li>
              <li>· Each question is grounded in the released topic material.</li>
            </ul>

            <div className="flex flex-wrap gap-2">
              {quiz.open_attempt_id ? (
                <Button
                  onClick={() =>
                    router.push(`/student/tests/attempts/${quiz.open_attempt_id}`)
                  }
                >
                  Resume test
                </Button>
              ) : (
                <Button
                  disabled={start.isPending || limitReached || quiz.question_count === 0}
                  onClick={() => {
                    setActionError(null);
                    start.mutate();
                  }}
                >
                  {limitReached
                    ? "Attempt limit reached"
                    : start.isPending
                      ? "Starting..."
                      : "Start test"}
                </Button>
              )}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Integrity</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm text-ink-600">
            <p>
              Answer keys are never sent to your browser before submission. The server grades every
              attempt with the stored key.
            </p>
            <p>
              Your open attempt is unique per test. Refreshing or leaving the page does not create a
              second attempt.
            </p>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
