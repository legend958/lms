"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorBanner, PageHeader, Spinner } from "@/components/ui/feedback";
import { ApiError, apiFetch } from "@/lib/api";
import { useMe, useStudentAttempts, useStudentQuizzes } from "@/lib/hooks";
import { formatDateTime } from "@/lib/utils";
import type { StudentAttemptState, StudentQuiz } from "@/lib/types";

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

export function StudentTests() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { data: me } = useMe();
  const [courseId, setCourseId] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);

  const quizzesQuery = useStudentQuizzes(courseId || undefined);
  const attemptsQuery = useStudentAttempts();

  const start = useMutation({
    mutationFn: (quizId: string) =>
      apiFetch<StudentAttemptState>(`/api/v1/student/quizzes/${quizId}/attempts`, {
        method: "POST",
      }),
    onSuccess: (state) => {
      void queryClient.invalidateQueries({ queryKey: ["student-quizzes"] });
      void queryClient.invalidateQueries({ queryKey: ["student-attempts"] });
      router.push(`/student/tests/attempts/${state.attempt_id}`);
    },
    onError: (error) => setActionError(errorMessage(error, "Could not start this quiz.")),
  });

  const quizzes = quizzesQuery.data ?? [];
  const attempts = attemptsQuery.data?.items ?? [];
  const openAttempts = attempts.filter((attempt) => attempt.open);
  const submittedAttempts = attempts.filter((attempt) => !attempt.open);
  const courses = me?.courses ?? [];

  function quizAction(quiz: StudentQuiz) {
    if (quiz.open_attempt_id) {
      return (
        <Button
          onClick={() => router.push(`/student/tests/attempts/${quiz.open_attempt_id}`)}
        >
          Resume test
        </Button>
      );
    }
    const limitReached = quiz.attempt_limit > 0 && quiz.attempts_used >= quiz.attempt_limit;
    return (
      <Button
        disabled={start.isPending || limitReached || quiz.question_count === 0}
        onClick={() => {
          setActionError(null);
          start.mutate(quiz.id);
        }}
      >
        {limitReached ? "Attempt limit reached" : start.isPending ? "Starting..." : "Start test"}
      </Button>
    );
  }

  return (
    <div>
      <PageHeader
        title="Tests"
        description="Quizzes released by your teachers. Every question comes from the course material."
        actions={
          <Link
            href="/student/dashboard"
            className="inline-flex h-10 items-center rounded-lg border border-ink-200 bg-white px-4 text-sm font-medium text-ink-800 shadow-sm hover:bg-ink-50"
          >
            Back to dashboard
          </Link>
        }
      />

      <Card className="mb-5">
        <CardContent className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-2 text-xs text-ink-600">
            <Badge tone="info">{quizzes.length} available</Badge>
            <Badge tone="warning">{openAttempts.length} in progress</Badge>
            <Badge tone="success">{submittedAttempts.length} submitted</Badge>
          </div>
          <label className="flex items-center gap-2 text-xs font-medium text-ink-500">
            Course
            <select
              value={courseId}
              onChange={(event) => setCourseId(event.target.value)}
              className="h-9 rounded-lg border border-ink-200 bg-white px-2 text-xs text-ink-800 shadow-sm focus:border-indigo-400 focus:outline-none"
            >
              <option value="">All courses</option>
              {courses.map((course) => (
                <option key={course.id} value={course.id}>
                  {course.code} — {course.title}
                </option>
              ))}
            </select>
          </label>
        </CardContent>
      </Card>

      {actionError ? (
        <div className="mb-4">
          <ErrorBanner message={actionError} />
        </div>
      ) : null}

      {quizzesQuery.error ? (
        <ErrorBanner message="Could not load your tests. Try again in a moment." />
      ) : null}
      {quizzesQuery.isLoading ? <Spinner label="Loading tests" /> : null}

      {!quizzesQuery.isLoading && !quizzesQuery.error && quizzes.length === 0 ? (
        <EmptyState
          title="No tests available"
          description="A test appears once your teacher publishes its quiz and the topic is released."
        />
      ) : null}

      <div className="mb-8 space-y-3">
        {quizzes.map((quiz) => {
          const answered = quiz.attempts_used;
          return (
            <Card key={quiz.id}>
              <CardContent className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <h2 className="text-sm font-semibold text-ink-900">{quiz.title}</h2>
                    {quiz.open_attempt_id ? <Badge tone="warning">In progress</Badge> : null}
                    {quiz.best_score !== null ? (
                      <Badge tone={quiz.best_score >= quiz.pass_threshold ? "success" : "danger"}>
                        Best {quiz.best_score}%
                      </Badge>
                    ) : null}
                  </div>
                  <p className="mt-1 text-xs text-ink-500">
                    {quiz.course.code} · {quiz.course.title} · {quiz.question_count} questions ·
                    pass {quiz.pass_threshold}%
                  </p>
                  <p className="mt-1 text-xs text-ink-500">
                    {quiz.attempt_limit === 0
                      ? `${answered} attempt${answered === 1 ? "" : "s"} used`
                      : `${answered} of ${quiz.attempt_limit} attempts used`}
                  </p>
                </div>
                <div className="flex shrink-0 gap-2">{quizAction(quiz)}</div>
              </CardContent>
            </Card>
          );
        })}
      </div>

      <Card>
        <CardHeader className="flex items-center justify-between gap-3">
          <CardTitle>Your attempts</CardTitle>
          <span className="text-xs text-ink-500">
            {attemptsQuery.isFetching ? "Refreshing…" : "Newest first"}
          </span>
        </CardHeader>

        {attemptsQuery.isLoading ? (
          <CardContent>
            <Spinner label="Loading attempts" />
          </CardContent>
        ) : attempts.length === 0 ? (
          <CardContent>
            <p className="text-sm text-ink-500">
              No attempts yet. Start a test above; your answers autosave until you submit.
            </p>
          </CardContent>
        ) : (
          <div className="divide-y divide-ink-100">
            {attempts.map((attempt) => (
              <div
                key={attempt.id}
                className="flex flex-col gap-2 px-5 py-4 sm:flex-row sm:items-center sm:justify-between"
              >
                <div className="min-w-0">
                  <p className="text-sm font-medium text-ink-900">{attempt.quiz_title}</p>
                  <p className="mt-0.5 text-xs text-ink-500">
                    {attempt.course.code} · started {formatDateTime(attempt.started_at)}
                    {attempt.submitted_at
                      ? ` · submitted ${formatDateTime(attempt.submitted_at)}`
                      : ""}
                  </p>
                </div>
                <div className="flex items-center gap-3">
                  {attempt.open ? (
                    <Badge tone="warning">In progress</Badge>
                  ) : (
                    <>
                      <Badge tone={attempt.passed ? "success" : "danger"}>
                        {attempt.percentage}%
                      </Badge>
                      <span className="text-xs text-ink-500">
                        {attempt.score}/{attempt.max_score}
                      </span>
                    </>
                  )}
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => router.push(`/student/tests/attempts/${attempt.id}`)}
                  >
                    {attempt.open ? "Resume" : "View result"}
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
