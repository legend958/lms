"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorBanner, Spinner } from "@/components/ui/feedback";
import { ApiError, apiFetch } from "@/lib/api";
import { useStudentAttempt, useStudentAttemptResult } from "@/lib/hooks";
import { cn, formatDateTime } from "@/lib/utils";
import type {
  AnswerInput,
  StudentAttemptResult,
  StudentAttemptState,
  StudentQuestion,
} from "@/lib/types";

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

function AnswerOptions({
  question,
  selected,
  disabled,
  onSelect,
}: {
  question: StudentQuestion;
  selected: number | undefined;
  disabled?: boolean;
  onSelect: (option: number) => void;
}) {
  return (
    <div className="space-y-2">
      {question.options.map((option, index) => {
        const active = selected === index;
        return (
          <button
            key={`${question.id}-${index}`}
            type="button"
            disabled={disabled}
            onClick={() => onSelect(index)}
            className={cn(
              "flex w-full items-start gap-3 rounded-xl border px-4 py-3 text-left text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-60",
              active
                ? "border-indigo-400 bg-indigo-50 text-indigo-950"
                : "border-ink-200 bg-white text-ink-700 hover:border-indigo-300 hover:bg-ink-50",
            )}
          >
            <span
              className={cn(
                "mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-xs font-semibold",
                active
                  ? "border-indigo-500 bg-indigo-600 text-white"
                  : "border-ink-300 text-ink-500",
              )}
            >
              {String.fromCharCode(65 + index)}
            </span>
            <span className="leading-relaxed">{option}</span>
          </button>
        );
      })}
    </div>
  );
}

function Runner({
  attempt,
  onSubmitted,
}: {
  attempt: StudentAttemptState;
  onSubmitted: (result: StudentAttemptResult) => void;
}) {
  const queryClient = useQueryClient();
  const [answers, setAnswers] = useState<Record<string, number>>({});
  const [index, setIndex] = useState(0);
  const [hydrated, setHydrated] = useState(false);
  const [saveStatus, setSaveStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [submitError, setSubmitError] = useState<string | null>(null);
  const seeded = useRef(false);

  useEffect(() => {
    if (seeded.current) return;
    seeded.current = true;
    const next: Record<string, number> = {};
    for (const answer of attempt.answers) {
      if (answer.selected_option !== null) next[answer.question_id] = answer.selected_option;
    }
    setAnswers(next);
    setHydrated(true);
  }, [attempt]);

  const save = useMutation({
    mutationFn: (payload: AnswerInput[]) =>
      apiFetch<{ saved: number; answered: number; total: number }>(
        `/api/v1/student/attempts/${attempt.attempt_id}`,
        { method: "PATCH", body: JSON.stringify({ answers: payload }) },
      ),
  });

  useEffect(() => {
    if (!hydrated) return;
    const payload = Object.entries(answers).map(([question_id, selected_option]) => ({
      question_id,
      selected_option,
    }));
    setSaveStatus("saving");
    const timer = setTimeout(() => {
      save.mutate(payload, {
        onSuccess: () => setSaveStatus("saved"),
        onError: () => setSaveStatus("error"),
      });
    }, 700);
    return () => clearTimeout(timer);
    // ``save`` is a stable React Query mutation; only the answers change here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [answers, hydrated]);

  const submit = useMutation({
    mutationFn: () =>
      apiFetch<StudentAttemptResult>(`/api/v1/student/attempts/${attempt.attempt_id}/submit`, {
        method: "POST",
        body: JSON.stringify({
          answers: Object.entries(answers).map(([question_id, selected_option]) => ({
            question_id,
            selected_option,
          })),
        }),
      }),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: ["student-attempts"] });
      void queryClient.invalidateQueries({ queryKey: ["student-quizzes"] });
      void queryClient.invalidateQueries({ queryKey: ["student-attempt", attempt.attempt_id] });
      onSubmitted(result);
    },
    onError: (error) => setSubmitError(errorMessage(error, "Could not submit this attempt.")),
  });

  const questions = attempt.questions;
  const answered = questions.filter((question) => answers[question.id] !== undefined).length;
  const current = questions[index];
  const progress = questions.length === 0 ? 0 : Math.round((answered / questions.length) * 100);

  function choose(option: number) {
    if (!current) return;
    setAnswers((previous) => ({ ...previous, [current.id]: option }));
  }

  function handleSubmit() {
    const remaining = questions.length - answered;
    const message =
      remaining > 0
        ? `Submit with ${remaining} unanswered question${remaining === 1 ? "" : "s"}? You cannot change answers after submitting.`
        : "Submit this attempt? You cannot change answers after submitting.";
    if (window.confirm(message)) {
      setSubmitError(null);
      submit.mutate();
    }
  }

  return (
    <div>
      <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <Link
            href="/student/tests"
            className="text-xs font-semibold uppercase tracking-wide text-indigo-600 hover:text-indigo-700"
          >
            Back to tests
          </Link>
          <h1 className="mt-2 text-2xl font-semibold tracking-tight text-ink-950">
            {attempt.quiz.title}
          </h1>
          <p className="mt-1 text-sm text-ink-500">
            {attempt.quiz.course.code} · started {formatDateTime(attempt.started_at)}
          </p>
        </div>
        <div className="flex items-center gap-2 text-xs text-ink-500">
          {saveStatus === "saving" ? <Spinner label="Saving answers" /> : null}
          {saveStatus === "saved" ? <Badge tone="success">Answers saved</Badge> : null}
          {saveStatus === "error" ? <Badge tone="danger">Autosave failed</Badge> : null}
          <Badge tone="info">
            {answered}/{questions.length} answered
          </Badge>
        </div>
      </div>

      {submitError ? (
        <div className="mb-4">
          <ErrorBanner message={submitError} />
        </div>
      ) : null}

      <div className="mb-4 h-2 overflow-hidden rounded-full bg-ink-100">
        <div
          className="h-full rounded-full bg-indigo-500 transition-all"
          style={{ width: `${progress}%` }}
        />
      </div>

      <div className="grid gap-5 lg:grid-cols-[1fr_220px]">
        <Card>
          <CardHeader className="flex flex-wrap items-center justify-between gap-2">
            <CardTitle>
              Question {index + 1} of {questions.length}
            </CardTitle>
            <span className="text-xs text-ink-500">
              {answers[current?.id ?? ""] !== undefined ? "Answered" : "Not answered yet"}
            </span>
          </CardHeader>
          <CardContent className="space-y-4">
            {current ? (
              <>
                <p className="text-sm font-medium leading-relaxed text-ink-900">{current.stem}</p>
                <AnswerOptions
                  question={current}
                  selected={answers[current.id]}
                  onSelect={choose}
                />
              </>
            ) : (
              <p className="text-sm text-ink-500">This quiz has no questions.</p>
            )}

            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-ink-100 pt-4">
              <div className="flex gap-2">
                <Button
                  variant="secondary"
                  disabled={index === 0}
                  onClick={() => setIndex((value) => Math.max(0, value - 1))}
                >
                  Previous
                </Button>
                <Button
                  variant="secondary"
                  disabled={index >= questions.length - 1}
                  onClick={() => setIndex((value) => Math.min(questions.length - 1, value + 1))}
                >
                  Next
                </Button>
              </div>
              <Button
                variant="danger"
                disabled={submit.isPending || questions.length === 0}
                onClick={handleSubmit}
              >
                {submit.isPending ? "Submitting..." : "Submit test"}
              </Button>
            </div>
          </CardContent>
        </Card>

        <Card className="h-fit">
          <CardHeader>
            <CardTitle>Question map</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-5 gap-2">
              {questions.map((question, questionIndex) => {
                const isAnswered = answers[question.id] !== undefined;
                return (
                  <button
                    key={question.id}
                    type="button"
                    aria-label={`Go to question ${questionIndex + 1}`}
                    onClick={() => setIndex(questionIndex)}
                    className={cn(
                      "flex h-9 items-center justify-center rounded-lg border text-xs font-semibold transition-colors",
                      questionIndex === index
                        ? "border-indigo-500 bg-indigo-600 text-white"
                        : isAnswered
                          ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                          : "border-ink-200 bg-white text-ink-500 hover:border-indigo-300",
                    )}
                  >
                    {questionIndex + 1}
                  </button>
                );
              })}
            </div>
            <p className="mt-3 text-xs text-ink-500">
              Green means answered. Answers autosave as you go; submit to finish.
            </p>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

export function AttemptResultView({ result }: { result: StudentAttemptResult }) {
  return (
    <div>
      <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <Link
            href="/student/tests"
            className="text-xs font-semibold uppercase tracking-wide text-indigo-600 hover:text-indigo-700"
          >
            Back to tests
          </Link>
          <h1 className="mt-2 text-2xl font-semibold tracking-tight text-ink-950">
            {result.quiz_title}
          </h1>
          <p className="mt-1 text-sm text-ink-500">
            Submitted {formatDateTime(result.submitted_at)}
          </p>
        </div>
        <Badge tone={result.passed ? "success" : "danger"}>
          {result.passed ? "Passed" : "Not passed"}
        </Badge>
      </div>

      <Card className="mb-6">
        <CardContent className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-ink-500">Your score</p>
            <p className="mt-1 text-4xl font-semibold text-ink-950">{result.percentage}%</p>
            <p className="mt-1 text-sm text-ink-500">
              {result.score} of {result.max_score} correct · pass mark {result.pass_threshold}%
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Link
              href={`/student/tests/${result.quiz_id}`}
              className="inline-flex h-10 items-center rounded-lg border border-ink-200 bg-white px-4 text-sm font-medium text-ink-800 shadow-sm hover:bg-ink-50"
            >
              Back to quiz
            </Link>
            <Link
              href="/student/tests"
              className="inline-flex h-10 items-center rounded-lg bg-indigo-600 px-4 text-sm font-medium text-white shadow-sm hover:bg-indigo-700"
            >
              All tests
            </Link>
          </div>
        </CardContent>
      </Card>

      <div className="space-y-4">
        {result.review.map((item, index) => (
          <Card key={item.question_id}>
            <CardHeader className="flex flex-wrap items-center justify-between gap-2">
              <CardTitle>
                {index + 1}. {item.stem}
              </CardTitle>
              <Badge tone={item.is_correct ? "success" : "danger"}>
                {item.is_correct ? "Correct" : item.selected_option === null ? "Skipped" : "Wrong"}
              </Badge>
            </CardHeader>
            <CardContent className="space-y-4">
              <ul className="space-y-2">
                {item.options.map((option, optionIndex) => {
                  const isCorrect = optionIndex === item.correct_option;
                  const isSelected = optionIndex === item.selected_option;
                  return (
                    <li
                      key={`${item.question_id}-${optionIndex}`}
                      className={cn(
                        "flex items-start gap-3 rounded-lg border px-3 py-2 text-sm",
                        isCorrect
                          ? "border-emerald-300 bg-emerald-50 text-emerald-900"
                          : isSelected
                            ? "border-rose-300 bg-rose-50 text-rose-900"
                            : "border-ink-200 bg-white text-ink-700",
                      )}
                    >
                      <span className="text-xs font-semibold text-ink-500">
                        {String.fromCharCode(65 + optionIndex)}
                      </span>
                      <span className="flex-1 leading-relaxed">{option}</span>
                      {isCorrect ? (
                        <span className="text-[11px] font-semibold uppercase text-emerald-700">
                          correct
                        </span>
                      ) : null}
                      {isSelected && !isCorrect ? (
                        <span className="text-[11px] font-semibold uppercase text-rose-700">
                          your answer
                        </span>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
              <p className="text-sm text-ink-600">
                <span className="font-medium text-ink-700">Why: </span>
                {item.explanation}
              </p>
              <p className="text-xs text-ink-500">
                Source chunk {item.source_chunk_id.slice(0, 8)}…
              </p>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}

export function StudentAttemptRunner({ attemptId }: { attemptId: string }) {
  const { data: attempt, isLoading, error } = useStudentAttempt(attemptId);
  const [result, setResult] = useState<StudentAttemptResult | null>(null);
  const submitted = Boolean(attempt?.submitted_at);
  const resultQuery = useStudentAttemptResult(attemptId, submitted);

  if (isLoading) return <Spinner label="Loading attempt" />;
  if (error) return <ErrorBanner message="Could not load this attempt." />;
  if (!attempt) return <ErrorBanner message="Attempt not found." />;

  if (result) return <AttemptResultView result={result} />;

  if (attempt.submitted_at) {
    if (resultQuery.isLoading) return <Spinner label="Loading your result" />;
    if (resultQuery.error || !resultQuery.data) {
      return <ErrorBanner message="Could not load your result." />;
    }
    return <AttemptResultView result={resultQuery.data} />;
  }

  return <Runner attempt={attempt} onSubmitted={setResult} />;
}
