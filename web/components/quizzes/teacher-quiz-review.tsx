"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorBanner, Spinner } from "@/components/ui/feedback";
import { Field, Input, Select, Textarea } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { ApiError, apiFetch } from "@/lib/api";
import { useTeacherQuiz } from "@/lib/hooks";
import { cn, formatDateTime } from "@/lib/utils";
import type {
  QuestionDifficulty,
  QuizDetail,
  QuizRecord,
  TeacherQuestion,
} from "@/lib/types";

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

function StatusBadge({ quiz }: { quiz: QuizDetail }) {
  if (quiz.status === "published") return <Badge tone="success">Published</Badge>;
  if (quiz.status === "archived") return <Badge tone="neutral">Archived</Badge>;
  return <Badge tone="warning">Draft</Badge>;
}

function GenerationBanner({ quiz }: { quiz: QuizDetail }) {
  if (quiz.generation_status === "failed") {
    return (
      <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
        <p className="font-semibold">Generation failed</p>
        <p className="mt-1">{quiz.generation_error || "The model did not return valid questions."}</p>
        {quiz.status === "draft" ? (
          <p className="mt-1 text-xs">
            Use <span className="font-semibold">Regenerate</span> to try again with the same source
            chunks.
          </p>
        ) : null}
      </div>
    );
  }
  if (quiz.generation_status === "queued" || quiz.generation_status === "generating") {
    return (
      <div className="flex items-center gap-3 rounded-lg border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-sky-800">
        <span className="h-4 w-4 animate-spin rounded-full border-2 border-sky-200 border-t-sky-600" />
        {quiz.generation_status === "queued"
          ? "Queued for generation. Questions appear here as soon as the worker finishes."
          : "Generating grounded questions from the topic chunks…"}
      </div>
    );
  }
  return null;
}

interface QuestionForm {
  stem: string;
  options: string[];
  correctOption: number;
  explanation: string;
  difficulty: QuestionDifficulty;
  approved: boolean;
}

function questionToForm(question: TeacherQuestion): QuestionForm {
  return {
    stem: question.stem,
    options: [...question.options],
    correctOption: question.correct_option,
    explanation: question.explanation,
    difficulty: question.difficulty,
    approved: question.approved,
  };
}

export function TeacherQuizReview({ quizId }: { quizId: string }) {
  const queryClient = useQueryClient();
  const { data: quiz, isLoading, error } = useTeacherQuiz(quizId);

  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [settingsOpen, setSettingsOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [passThreshold, setPassThreshold] = useState("60");
  const [attemptLimit, setAttemptLimit] = useState("0");
  const [autoApprove, setAutoApprove] = useState(false);

  const [editing, setEditing] = useState<TeacherQuestion | null>(null);
  const [form, setForm] = useState<QuestionForm | null>(null);

  useEffect(() => {
    if (!quiz || settingsOpen) return;
    setTitle(quiz.title);
    setPassThreshold(String(quiz.pass_threshold));
    setAttemptLimit(String(quiz.attempt_limit));
    setAutoApprove(quiz.auto_approve);
  }, [quiz, settingsOpen]);

  function invalidate() {
    void queryClient.invalidateQueries({ queryKey: ["teacher-quiz", quizId] });
    void queryClient.invalidateQueries({ queryKey: ["teacher-quizzes"] });
  }

  const updateSettings = useMutation({
    mutationFn: () =>
      apiFetch<QuizDetail>(`/api/v1/teacher/quizzes/${quizId}`, {
        method: "PATCH",
        body: JSON.stringify({
          title: title.trim(),
          pass_threshold: Number(passThreshold),
          attempt_limit: Number(attemptLimit),
          auto_approve: autoApprove,
        }),
      }),
    onSuccess: () => {
      setSettingsOpen(false);
      setActionError(null);
      setNotice("Quiz settings saved.");
      invalidate();
    },
    onError: (err) => setActionError(errorMessage(err, "Could not save the quiz settings.")),
  });

  const approve = useMutation({
    mutationFn: () =>
      apiFetch<QuizDetail>(`/api/v1/teacher/quizzes/${quizId}/approve`, { method: "POST" }),
    onSuccess: (result) => {
      setActionError(null);
      setNotice(
        `Quiz published with ${result.approved_count} approved question${
          result.approved_count === 1 ? "" : "s"
        }.`,
      );
      invalidate();
    },
    onError: (err) => setActionError(errorMessage(err, "Could not publish the quiz.")),
  });

  const regenerate = useMutation({
    mutationFn: () =>
      apiFetch<QuizRecord>(`/api/v1/teacher/quizzes/${quizId}/regenerate`, { method: "POST" }),
    onSuccess: () => {
      setActionError(null);
      setNotice("Regeneration queued. New questions will replace the current draft.");
      invalidate();
    },
    onError: (err) => setActionError(errorMessage(err, "Could not regenerate the quiz.")),
  });

  const updateQuestion = useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: Record<string, unknown> }) =>
      apiFetch<TeacherQuestion>(`/api/v1/teacher/questions/${id}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      setEditing(null);
      setForm(null);
      setActionError(null);
      setNotice("Question saved. Edited questions return to review.");
      invalidate();
    },
    onError: (err) => setActionError(errorMessage(err, "Could not save the question.")),
  });

  const deleteQuestion = useMutation({
    mutationFn: (id: string) =>
      apiFetch<void>(`/api/v1/teacher/questions/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      setActionError(null);
      setNotice("Question deleted.");
      invalidate();
    },
    onError: (err) => setActionError(errorMessage(err, "Could not delete the question.")),
  });

  function openSettings() {
    if (!quiz) return;
    setTitle(quiz.title);
    setPassThreshold(String(quiz.pass_threshold));
    setAttemptLimit(String(quiz.attempt_limit));
    setAutoApprove(quiz.auto_approve);
    setSettingsOpen(true);
  }

  function openEdit(question: TeacherQuestion) {
    setEditing(question);
    setForm(questionToForm(question));
  }

  function saveQuestion() {
    if (!editing || !form) return;
    const options = form.options.map((option) => option.trim());
    if (form.stem.trim().length < 10) {
      setActionError("Question stems must be at least 10 characters.");
      return;
    }
    if (options.some((option) => option.length === 0)) {
      setActionError("Every option needs text.");
      return;
    }
    updateQuestion.mutate({
      id: editing.id,
      payload: {
        stem: form.stem.trim(),
        options,
        correct_option: form.correctOption,
        explanation: form.explanation.trim(),
        difficulty: form.difficulty,
        approved: form.approved,
      },
    });
  }

  if (isLoading) return <Spinner label="Loading quiz" />;
  if (error) return <ErrorBanner message="Could not load this quiz." />;
  if (!quiz) return <ErrorBanner message="Quiz not found." />;

  const isDraft = quiz.status === "draft";
  const canApprove = isDraft && quiz.question_count > 0 && !approve.isPending;

  return (
    <div>
      <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <Link
            href="/teacher/quizzes"
            className="text-xs font-semibold uppercase tracking-wide text-indigo-600 hover:text-indigo-700"
          >
            Back to quizzes
          </Link>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <h1 className="text-2xl font-semibold tracking-tight text-ink-950">{quiz.title}</h1>
            <StatusBadge quiz={quiz} />
            {quiz.needs_review ? <Badge tone="warning">Needs review</Badge> : null}
            {quiz.auto_approve ? <Badge tone="indigo">Auto-approve</Badge> : null}
          </div>
          <p className="mt-1 text-sm text-ink-500">
            Topic: {quiz.topic_title} · {quiz.question_count} questions ·{" "}
            {quiz.attempt_count} attempts
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" onClick={openSettings}>
            Settings
          </Button>
          <Button
            variant="secondary"
            disabled={!isDraft || regenerate.isPending}
            onClick={() => {
              if (
                window.confirm(
                  "Regenerate all draft questions? Any edits to the current draft are replaced.",
                )
              ) {
                regenerate.mutate();
              }
            }}
          >
            {regenerate.isPending ? "Queueing..." : "Regenerate"}
          </Button>
          <Button
            disabled={!canApprove}
            onClick={() => {
              if (
                window.confirm(
                  `Publish this quiz? ${quiz.question_count} question${
                    quiz.question_count === 1 ? "" : "s"
                  } become visible to students once the topic is released.`,
                )
              ) {
                approve.mutate();
              }
            }}
          >
            {approve.isPending ? "Publishing..." : "Approve & publish"}
          </Button>
        </div>
      </div>

      {actionError ? (
        <div className="mb-4">
          <ErrorBanner message={actionError} />
        </div>
      ) : null}
      {notice ? (
        <div className="mb-4 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
          {notice}
        </div>
      ) : null}

      <div className="mb-4">
        <GenerationBanner quiz={quiz} />
      </div>

      <div className="mb-6 grid gap-4 sm:grid-cols-4">
        {[
          {
            label: "Questions",
            value: `${quiz.approved_count}/${quiz.question_count}`,
            hint: "Approved / total",
          },
          { label: "Pass mark", value: `${quiz.pass_threshold}%`, hint: "Server-side grading" },
          {
            label: "Attempt limit",
            value: quiz.attempt_limit === 0 ? "Unlimited" : String(quiz.attempt_limit),
            hint: "Per student",
          },
          { label: "Updated", value: formatDateTime(quiz.updated_at), hint: "Last change" },
        ].map((stat) => (
          <Card key={stat.label}>
            <CardContent>
              <p className="text-xs font-semibold uppercase tracking-wide text-ink-500">
                {stat.label}
              </p>
              <p className="mt-2 text-lg font-semibold text-ink-950">{stat.value}</p>
              <p className="mt-1 text-xs text-ink-500">{stat.hint}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      {!isDraft ? (
        <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          This quiz is published. Questions are locked so submitted and future attempts stay
          comparable. Regenerate is only available for drafts.
        </div>
      ) : null}

      <Card>
        <CardHeader className="flex items-center justify-between gap-3">
          <CardTitle>Questions</CardTitle>
          <span className="text-xs text-ink-500">Correct answers are teacher-only</span>
        </CardHeader>

        {quiz.questions.length === 0 ? (
          <CardContent>
            <p className="text-sm text-ink-500">
              {quiz.generation_status === "queued" || quiz.generation_status === "generating"
                ? "Questions will appear when generation finishes."
                : "No questions yet. Regenerate to ask the model again, or check the topic has source chunks."}
            </p>
          </CardContent>
        ) : (
          <div className="divide-y divide-ink-100">
            {quiz.questions.map((question, index) => (
              <div key={question.id} className="px-5 py-5">
                <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-xs font-semibold text-ink-500">
                        {String(index + 1).padStart(2, "0")}
                      </span>
                      <p className="text-sm font-semibold text-ink-900">{question.stem}</p>
                      <Badge tone={question.approved ? "success" : "warning"}>
                        {question.approved ? "Approved" : "Pending"}
                      </Badge>
                      <Badge tone="neutral">{question.difficulty}</Badge>
                    </div>

                    <ul className="mt-3 grid gap-2 sm:grid-cols-2">
                      {question.options.map((option, optionIndex) => (
                        <li
                          key={`${question.id}-${optionIndex}`}
                          className={cn(
                            "rounded-lg border px-3 py-2 text-sm",
                            optionIndex === question.correct_option
                              ? "border-emerald-300 bg-emerald-50 text-emerald-900"
                              : "border-ink-200 bg-white text-ink-700",
                          )}
                        >
                          <span className="mr-2 text-xs font-semibold text-ink-500">
                            {String.fromCharCode(65 + optionIndex)}
                          </span>
                          {option}
                          {optionIndex === question.correct_option ? (
                            <span className="ml-2 text-xs font-semibold uppercase text-emerald-700">
                              correct
                            </span>
                          ) : null}
                        </li>
                      ))}
                    </ul>

                    <p className="mt-3 text-sm text-ink-600">
                      <span className="font-medium text-ink-700">Explanation: </span>
                      {question.explanation}
                    </p>
                    <p className="mt-1 text-xs text-ink-500">
                      Source chunk {question.source_chunk_id.slice(0, 8)}…
                    </p>
                  </div>

                  <div className="flex shrink-0 gap-1">
                    <Button
                      size="sm"
                      variant="secondary"
                      disabled={!isDraft}
                      onClick={() => openEdit(question)}
                    >
                      Edit
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="text-rose-600 hover:bg-rose-50 hover:text-rose-700"
                      disabled={!isDraft || deleteQuestion.isPending}
                      onClick={() => {
                        if (window.confirm("Delete this question from the draft?")) {
                          deleteQuestion.mutate(question.id);
                        }
                      }}
                    >
                      Delete
                    </Button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      <Modal
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        title="Quiz settings"
        description="Settings apply to future attempts. Submitted attempts keep their stored score."
        footer={
          <>
            <Button variant="secondary" onClick={() => setSettingsOpen(false)}>
              Cancel
            </Button>
            <Button
              disabled={updateSettings.isPending || title.trim().length < 3}
              onClick={() => updateSettings.mutate()}
            >
              {updateSettings.isPending ? "Saving..." : "Save settings"}
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field label="Quiz title">
            <Input
              value={title}
              maxLength={200}
              onChange={(event) => setTitle(event.target.value)}
            />
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Pass threshold (%)" hint="0-100. Default is 60.">
              <Input
                type="number"
                min={0}
                max={100}
                value={passThreshold}
                onChange={(event) => setPassThreshold(event.target.value)}
              />
            </Field>
            <Field label="Attempt limit" hint="0 means unlimited.">
              <Input
                type="number"
                min={0}
                max={1000}
                value={attemptLimit}
                onChange={(event) => setAttemptLimit(event.target.value)}
              />
            </Field>
          </div>
          <label className="flex items-start gap-2 rounded-lg border border-ink-200 px-3 py-3 text-sm text-ink-700">
            <input
              type="checkbox"
              className="mt-0.5 h-4 w-4 rounded border-ink-300 text-indigo-600"
              checked={autoApprove}
              onChange={(event) => setAutoApprove(event.target.checked)}
            />
            <span>
              Auto-approve generated questions
              <span className="mt-0.5 block text-xs text-ink-500">
                Off by default. When on, a successful regeneration publishes the quiz without
                teacher review.
              </span>
            </span>
          </label>
        </div>
      </Modal>

      <Modal
        open={Boolean(editing && form)}
        onClose={() => {
          setEditing(null);
          setForm(null);
        }}
        title="Edit question"
        description="Saving a content change returns this question to pending review."
        className="max-w-2xl"
        footer={
          <>
            <Button
              variant="secondary"
              onClick={() => {
                setEditing(null);
                setForm(null);
              }}
            >
              Cancel
            </Button>
            <Button disabled={updateQuestion.isPending} onClick={saveQuestion}>
              {updateQuestion.isPending ? "Saving..." : "Save question"}
            </Button>
          </>
        }
      >
        {form ? (
          <div className="space-y-4">
            <Field label="Question stem" hint="10-500 characters.">
              <Textarea
                value={form.stem}
                maxLength={500}
                onChange={(event) => setForm({ ...form, stem: event.target.value })}
              />
            </Field>

            <div>
              <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-ink-500">
                Options
              </p>
              <div className="space-y-2">
                {form.options.map((option, index) => (
                  <label
                    key={index}
                    className={cn(
                      "flex items-center gap-3 rounded-lg border px-3 py-2",
                      form.correctOption === index
                        ? "border-emerald-300 bg-emerald-50"
                        : "border-ink-200",
                    )}
                  >
                    <input
                      type="radio"
                      name="correct-option"
                      checked={form.correctOption === index}
                      onChange={() => setForm({ ...form, correctOption: index })}
                    />
                    <span className="text-xs font-semibold text-ink-500">
                      {String.fromCharCode(65 + index)}
                    </span>
                    <Input
                      value={option}
                      maxLength={300}
                      onChange={(event) => {
                        const options = [...form.options];
                        options[index] = event.target.value;
                        setForm({ ...form, options });
                      }}
                    />
                  </label>
                ))}
              </div>
              <p className="mt-1 text-xs text-ink-500">
                Select the radio button next to the correct option.
              </p>
            </div>

            <Field label="Explanation" hint="Shown to students after submission.">
              <Textarea
                value={form.explanation}
                maxLength={1000}
                onChange={(event) => setForm({ ...form, explanation: event.target.value })}
              />
            </Field>

            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Difficulty">
                <Select
                  value={form.difficulty}
                  onChange={(event) =>
                    setForm({ ...form, difficulty: event.target.value as QuestionDifficulty })
                  }
                >
                  <option value="easy">Easy</option>
                  <option value="medium">Medium</option>
                  <option value="hard">Hard</option>
                </Select>
              </Field>
              <label className="flex items-end gap-2 pb-2 text-sm text-ink-700">
                <input
                  type="checkbox"
                  className="h-4 w-4 rounded border-ink-300 text-indigo-600"
                  checked={form.approved}
                  onChange={(event) => setForm({ ...form, approved: event.target.checked })}
                />
                Mark this question approved
              </label>
            </div>
          </div>
        ) : null}
      </Modal>
    </div>
  );
}
