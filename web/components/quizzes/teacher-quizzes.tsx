"use client";

import Link from "next/link";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { EmptyState, ErrorBanner, PageHeader, Spinner } from "@/components/ui/feedback";
import { useMe, useTeacherQuizzes } from "@/lib/hooks";
import { cn, formatDateTime } from "@/lib/utils";
import type { QuizGenerationStatus, QuizRecord, QuizStatus } from "@/lib/types";

function StatusBadge({ status }: { status: QuizStatus }) {
  if (status === "published") return <Badge tone="success">Published</Badge>;
  if (status === "archived") return <Badge tone="neutral">Archived</Badge>;
  return <Badge tone="warning">Draft</Badge>;
}

function GenerationBadge({ quiz }: { quiz: QuizRecord }) {
  const tone: Record<QuizGenerationStatus, "neutral" | "info" | "success" | "danger"> = {
    queued: "info",
    generating: "info",
    ready: "success",
    failed: "danger",
  };
  const label: Record<QuizGenerationStatus, string> = {
    queued: "Queued",
    generating: "Generating",
    ready: "Ready",
    failed: "Failed",
  };
  return <Badge tone={tone[quiz.generation_status]}>{label[quiz.generation_status]}</Badge>;
}

function QuizRow({ quiz }: { quiz: QuizRecord }) {
  return (
    <Link
      href={`/teacher/quizzes/${quiz.id}`}
      className="block px-5 py-4 transition-colors hover:bg-ink-50/70"
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-sm font-semibold text-ink-900">{quiz.title}</p>
            <StatusBadge status={quiz.status} />
            <GenerationBadge quiz={quiz} />
            {quiz.needs_review ? <Badge tone="warning">Needs review</Badge> : null}
            {quiz.auto_approve ? <Badge tone="indigo">Auto-approve</Badge> : null}
          </div>
          <p className="mt-1 text-xs text-ink-500">
            Topic: <span className="font-medium text-ink-700">{quiz.topic_title}</span>
          </p>
          {quiz.generation_error ? (
            <p className="mt-1 line-clamp-2 text-xs text-rose-600">{quiz.generation_error}</p>
          ) : null}
        </div>

        <div className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-500 sm:justify-end">
          <span>
            <span className="font-semibold text-ink-800">{quiz.approved_count}</span>/
            {quiz.question_count} approved
          </span>
          <span>{quiz.attempt_count} attempts</span>
          <span>Pass {quiz.pass_threshold}%</span>
          <span>Updated {formatDateTime(quiz.updated_at)}</span>
        </div>
      </div>
    </Link>
  );
}

export function TeacherQuizzes() {
  const { data: me } = useMe();
  const [courseId, setCourseId] = useState("");
  const { data, isLoading, error } = useTeacherQuizzes(courseId || undefined);

  const quizzes = data?.items ?? [];
  const courses = me?.courses ?? [];
  const draftCount = quizzes.filter((quiz) => quiz.status === "draft").length;
  const publishedCount = quizzes.filter((quiz) => quiz.status === "published").length;
  const reviewCount = quizzes.filter((quiz) => quiz.needs_review).length;

  return (
    <div>
      <PageHeader
        title="Quizzes"
        description="Review generated MCQs, edit the answer key, and publish a quiz to students."
        actions={
          <Link
            href="/teacher/documents"
            className="inline-flex h-10 items-center rounded-lg border border-ink-200 bg-white px-4 text-sm font-medium text-ink-800 shadow-sm hover:bg-ink-50"
          >
            Go to documents
          </Link>
        }
      />

      <div className="mb-5 grid gap-4 sm:grid-cols-3">
        {[
          { label: "Drafts", value: draftCount, hint: "Hidden from students" },
          { label: "Needs review", value: reviewCount, hint: "Approve or regenerate" },
          { label: "Published", value: publishedCount, hint: "Visible with released topics" },
        ].map((stat) => (
          <Card key={stat.label}>
            <CardContent>
              <p className="text-xs font-semibold uppercase tracking-wide text-ink-500">
                {stat.label}
              </p>
              <p className="mt-2 text-3xl font-semibold text-ink-950">{stat.value}</p>
              <p className="mt-1 text-xs text-ink-500">{stat.hint}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      <Card className="mb-5">
        <CardContent className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-ink-600">
            Quizzes are created when a topic is released. A quiz is only visible to students after
            it is published <span className="font-medium text-ink-800">and</span> its topic is
            released.
          </p>
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

      {error ? <ErrorBanner message="Could not load quizzes. Try again in a moment." /> : null}
      {isLoading ? <Spinner label="Loading quizzes" /> : null}

      {!isLoading && !error && quizzes.length === 0 ? (
        <EmptyState
          title="No quizzes yet"
          description="Release a topic with “Generate quiz” checked. The draft appears here for review before students can see it."
        />
      ) : null}

      {quizzes.length > 0 ? (
        <Card className={cn("divide-y divide-ink-100 overflow-hidden")}>
          {quizzes.map((quiz) => (
            <QuizRow key={quiz.id} quiz={quiz} />
          ))}
        </Card>
      ) : null}
    </div>
  );
}
