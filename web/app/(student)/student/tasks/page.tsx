"use client";

import Link from "next/link";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { EmptyState, ErrorBanner, PageHeader, Spinner } from "@/components/ui/feedback";
import { apiFetch } from "@/lib/api";
import { useMe } from "@/lib/hooks";
import { cn, formatDateTime } from "@/lib/utils";
import type {
  StudentTask,
  StudentTaskFilter,
  StudentTaskList,
  StudentTaskSort,
  StudentTaskUpdateResult,
  TaskProgressStatus,
} from "@/lib/types";

const TABS: Array<{ value: StudentTaskFilter; label: string }> = [
  { value: "all", label: "All" },
  { value: "incomplete", label: "Incomplete" },
  { value: "completed", label: "Completed" },
];

function TaskCard({
  task,
  pending,
  onToggle,
}: {
  task: StudentTask;
  pending: boolean;
  onToggle: (task: StudentTask) => void;
}) {
  const completed = task.status === "completed";

  return (
    <article
      className={cn(
        "rounded-xl border bg-white p-5 shadow-sm transition-colors",
        task.overdue ? "border-rose-200" : "border-ink-200",
        completed && "opacity-80",
      )}
    >
      <div className="flex items-start gap-4">
        <button
          type="button"
          role="checkbox"
          aria-checked={completed}
          aria-label={completed ? `Mark ${task.title} incomplete` : `Mark ${task.title} complete`}
          onClick={() => onToggle(task)}
          disabled={pending}
          className={cn(
            "mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-md border transition-colors disabled:opacity-50",
            completed
              ? "border-emerald-600 bg-emerald-600 text-white"
              : "border-ink-300 bg-white hover:border-indigo-500",
          )}
        >
          {completed ? (
            <svg viewBox="0 0 20 20" fill="currentColor" className="h-3.5 w-3.5" aria-hidden="true">
              <path
                fillRule="evenodd"
                d="M16.7 5.3a1 1 0 0 1 0 1.4l-7.5 7.5a1 1 0 0 1-1.4 0l-3.5-3.5a1 1 0 1 1 1.4-1.4l2.8 2.8 6.8-6.8a1 1 0 0 1 1.4 0Z"
                clipRule="evenodd"
              />
            </svg>
          ) : null}
        </button>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3
              className={cn(
                "text-sm font-semibold text-ink-900",
                completed && "text-ink-500 line-through",
              )}
            >
              {task.title}
            </h3>
            {completed ? (
              <Badge tone="success">Completed</Badge>
            ) : task.overdue ? (
              <Badge tone="danger">Overdue</Badge>
            ) : (
              <Badge tone="info">Incomplete</Badge>
            )}
          </div>

          <p className="mt-1 text-xs text-ink-500">
            <span className="font-medium text-ink-700">{task.course.code}</span> ·{" "}
            {task.course.title} · Week {task.week_no}
          </p>
          <p className={cn("mt-1 text-xs", task.overdue ? "font-medium text-rose-600" : "text-ink-500")}>
            Due {formatDateTime(task.due_at)}
            {task.completed_at ? ` · completed ${formatDateTime(task.completed_at)}` : ""}
          </p>

          {task.description ? (
            <p className="mt-2 line-clamp-2 text-sm leading-relaxed text-ink-600">
              {task.description}
            </p>
          ) : null}
        </div>
      </div>
    </article>
  );
}

export default function StudentTasksPage() {
  const { data: me } = useMe();
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<StudentTaskFilter>("all");
  const [courseId, setCourseId] = useState("");
  const [sort, setSort] = useState<StudentTaskSort>("due_at");

  const params = new URLSearchParams({ status, sort });
  if (courseId) params.set("course_id", courseId);
  const queryKey = ["student-tasks", status, courseId, sort] as const;

  const { data, isLoading, error } = useQuery({
    queryKey,
    queryFn: () => apiFetch<StudentTaskList>(`/api/v1/student/tasks?${params.toString()}`),
  });

  const toggle = useMutation({
    mutationFn: ({ id, next }: { id: string; next: TaskProgressStatus }) =>
      apiFetch<StudentTaskUpdateResult>(`/api/v1/student/tasks/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ status: next }),
      }),
    onMutate: async ({ id, next }) => {
      await queryClient.cancelQueries({ queryKey: ["student-tasks"] });
      const previous = queryClient.getQueryData<StudentTaskList>(queryKey);
      queryClient.setQueryData<StudentTaskList>(queryKey, (old) =>
        old
          ? {
              ...old,
              items: old.items.map((item) =>
                item.id === id
                  ? {
                      ...item,
                      status: next,
                      completed_at:
                        next === "completed" ? new Date().toISOString() : null,
                      overdue: next === "completed" ? false : item.overdue,
                    }
                  : item,
              ),
            }
          : old,
      );
      return { previous };
    },
    onError: (_error, _variables, context) => {
      if (context?.previous) {
        queryClient.setQueryData(queryKey, context.previous);
      }
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["student-tasks"] });
      void queryClient.invalidateQueries({ queryKey: ["student-dashboard"] });
    },
  });

  const tasks = data?.items ?? [];
  const courses = me?.courses ?? [];
  const filtered = status !== "all" || courseId !== "";

  return (
    <div>
      <PageHeader
        title="Tasks"
        description="Weekly work from every course, released by your teachers."
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
        <CardContent className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex flex-wrap gap-1 rounded-lg bg-ink-100 p-1">
            {TABS.map((tab) => (
              <button
                key={tab.value}
                type="button"
                onClick={() => setStatus(tab.value)}
                aria-pressed={status === tab.value}
                className={cn(
                  "rounded-md px-3 py-1.5 text-xs font-semibold transition-colors",
                  status === tab.value
                    ? "bg-white text-ink-900 shadow-sm"
                    : "text-ink-600 hover:text-ink-900",
                )}
              >
                {tab.label}
              </button>
            ))}
          </div>

          <div className="flex flex-wrap items-center gap-3">
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

            <label className="flex items-center gap-2 text-xs font-medium text-ink-500">
              Sort
              <select
                value={sort}
                onChange={(event) => setSort(event.target.value as StudentTaskSort)}
                className="h-9 rounded-lg border border-ink-200 bg-white px-2 text-xs text-ink-800 shadow-sm focus:border-indigo-400 focus:outline-none"
              >
                <option value="due_at">Due date</option>
                <option value="released_at">Recently released</option>
              </select>
            </label>
          </div>
        </CardContent>
      </Card>

      {error ? <ErrorBanner message="Could not load your tasks. Try again in a moment." /> : null}
      {isLoading ? <Spinner label="Loading tasks" /> : null}

      {!isLoading && !error && tasks.length === 0 ? (
        <EmptyState
          title={filtered ? "No tasks in this view" : "No tasks yet"}
          description={
            filtered
              ? "Try another status filter or course. Completed work stays available under the Completed tab."
              : "When a teacher releases a topic, its weekly task appears here with a due date."
          }
        />
      ) : null}

      <div className="space-y-3">
        {tasks.map((task) => (
          <TaskCard
            key={task.id}
            task={task}
            pending={toggle.isPending && toggle.variables?.id === task.id}
            onToggle={(item) =>
              toggle.mutate({
                id: item.id,
                next: item.status === "completed" ? "incomplete" : "completed",
              })
            }
          />
        ))}
      </div>
    </div>
  );
}
