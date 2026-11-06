"use client";

import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorBanner, PageHeader, Spinner } from "@/components/ui/feedback";
import { apiFetch } from "@/lib/api";
import { useMe } from "@/lib/hooks";
import { cn, formatDateTime } from "@/lib/utils";
import type { StudentDashboard, StudentTask } from "@/lib/types";

function StatCard({
  label,
  value,
  hint,
  tone = "default",
}: {
  label: string;
  value: number;
  hint: string;
  tone?: "default" | "danger";
}) {
  return (
    <Card>
      <CardContent>
        <p className="text-xs font-semibold uppercase tracking-wide text-ink-500">{label}</p>
        <p
          className={cn(
            "mt-2 text-3xl font-semibold",
            tone === "danger" && value > 0 ? "text-rose-600" : "text-ink-950",
          )}
        >
          {value}
        </p>
        <p className="mt-1 text-xs text-ink-500">{hint}</p>
      </CardContent>
    </Card>
  );
}

function DueTaskRow({ task }: { task: StudentTask }) {
  return (
    <li className="flex flex-wrap items-center justify-between gap-3 py-3">
      <div className="min-w-0">
        <p className="truncate text-sm font-medium text-ink-900">{task.title}</p>
        <p className="mt-0.5 text-xs text-ink-500">
          {task.course.code} · Week {task.week_no} · due {formatDateTime(task.due_at)}
        </p>
      </div>
      {task.overdue ? <Badge tone="danger">Overdue</Badge> : <Badge tone="info">Incomplete</Badge>}
    </li>
  );
}

export default function StudentDashboardPage() {
  const { data: me } = useMe();
  const queryClient = useQueryClient();

  const { data, isLoading, error } = useQuery({
    queryKey: ["student-dashboard"],
    queryFn: () => apiFetch<StudentDashboard>("/api/v1/student/dashboard"),
  });

  const markRead = useMutation({
    mutationFn: (id: string) =>
      apiFetch<void>(`/api/v1/announcements/${id}/read`, { method: "POST" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["student-dashboard"] }),
  });

  const counts = data?.counts ?? { pending: 0, completed: 0, overdue: 0 };
  const total = counts.pending + counts.completed;
  const percent = total > 0 ? Math.round((counts.completed / total) * 100) : 0;
  const courses = me?.courses.length ?? 0;

  return (
    <div>
      <PageHeader
        title={`Welcome back${me ? `, ${me.name.split(" ")[0]}` : ""}`}
        description="Your deadlines, progress, and announcements across every course."
        actions={
          <>
            <Link
              href="/student/tasks"
              className="inline-flex h-10 items-center rounded-lg bg-indigo-600 px-4 text-sm font-medium text-white shadow-sm hover:bg-indigo-700"
            >
              View all tasks
            </Link>
            <Link
              href="/student/library"
              className="inline-flex h-10 items-center rounded-lg border border-ink-200 bg-white px-4 text-sm font-medium text-ink-800 shadow-sm hover:bg-ink-50"
            >
              AI library
            </Link>
          </>
        }
      />

      {error ? <ErrorBanner message="Could not load your dashboard. Try again in a moment." /> : null}

      <div className="mb-4 grid gap-4 sm:grid-cols-3">
        <StatCard label="Pending" value={counts.pending} hint="Tasks left to complete" />
        <StatCard
          label="Completed"
          value={counts.completed}
          hint={`${counts.completed} of ${total} assigned`}
        />
        <StatCard
          label="Overdue"
          value={counts.overdue}
          hint="Past due and incomplete"
          tone="danger"
        />
      </div>

      <Card className="mb-6">
        <CardContent>
          <div className="flex items-center justify-between text-xs font-medium text-ink-500">
            <span>Overall progress</span>
            <span>
              {counts.completed} of {total} tasks · {percent}%
            </span>
          </div>
          <div
            className="mt-2 h-2 overflow-hidden rounded-full bg-ink-100"
            role="progressbar"
            aria-valuenow={percent}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label="Overall task completion"
          >
            <div
              className="h-2 rounded-full bg-gradient-to-r from-indigo-500 to-violet-500 transition-all"
              style={{ width: `${percent}%` }}
            />
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
        <section className="space-y-6">
          <Card>
            <CardHeader className="flex items-center justify-between">
              <CardTitle>Due this week</CardTitle>
              <Link
                href="/student/tasks?status=incomplete"
                className="text-xs font-medium text-indigo-600 hover:text-indigo-800"
              >
                See incomplete
              </Link>
            </CardHeader>
            <CardContent>
              {isLoading ? (
                <Spinner label="Loading your tasks" />
              ) : data && data.due_soon.length > 0 ? (
                <ul className="divide-y divide-ink-100">
                  {data.due_soon.map((task) => (
                    <DueTaskRow key={task.id} task={task} />
                  ))}
                </ul>
              ) : (
                <EmptyState
                  title="Nothing due right now"
                  description="When your teacher releases new work it will appear here with its due date."
                />
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex items-center justify-between">
              <CardTitle>Announcements</CardTitle>
              <span className="text-xs text-ink-500">Latest 3</span>
            </CardHeader>
            <CardContent>
              {isLoading ? (
                <Spinner label="Loading announcements" />
              ) : data && data.announcements.length > 0 ? (
                <ul className="space-y-3">
                  {data.announcements.map((announcement) => (
                    <li
                      key={announcement.id}
                      className={cn(
                        "rounded-lg border p-4",
                        announcement.read
                          ? "border-ink-200 bg-white"
                          : "border-indigo-200 bg-indigo-50/50",
                      )}
                    >
                      <div className="flex flex-wrap items-start justify-between gap-2">
                        <div className="min-w-0">
                          <div className="flex flex-wrap items-center gap-2">
                            <p className="text-sm font-semibold text-ink-900">
                              {announcement.title}
                            </p>
                            {!announcement.read ? <Badge tone="indigo">Unread</Badge> : null}
                          </div>
                          <p className="mt-1 text-xs text-ink-500">
                            {announcement.course_title ?? "Institution-wide"} ·{" "}
                            {announcement.author_name} · {formatDateTime(announcement.publish_at)}
                          </p>
                        </div>
                        {!announcement.read ? (
                          <button
                            type="button"
                            onClick={() => markRead.mutate(announcement.id)}
                            disabled={markRead.isPending}
                            className="text-xs font-medium text-indigo-600 hover:text-indigo-800 disabled:opacity-50"
                          >
                            Mark read
                          </button>
                        ) : null}
                      </div>
                      <p className="mt-2 line-clamp-3 whitespace-pre-line text-sm leading-relaxed text-ink-700">
                        {announcement.body}
                      </p>
                    </li>
                  ))}
                </ul>
              ) : (
                <EmptyState
                  title="No announcements yet"
                  description="When your teachers post an update it will show up here."
                />
              )}
            </CardContent>
          </Card>
        </section>

        <aside className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>My courses</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {courses > 0 ? (
                me?.courses.map((course) => (
                  <div key={course.id} className="rounded-lg border border-ink-100 px-3 py-2">
                    <div className="flex items-center justify-between gap-2">
                      <p className="text-sm font-medium text-ink-900">{course.title}</p>
                      <Badge tone="info">{course.code}</Badge>
                    </div>
                  </div>
                ))
              ) : (
                <p className="text-sm text-ink-500">
                  You are not enrolled in a course yet. Ask your administrator.
                </p>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Study tools</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 text-sm text-ink-600">
              <p>
                The AI library answers only from topics your teacher has released, with page
                citations.
              </p>
              <Link
                href="/student/library"
                className="inline-flex text-xs font-semibold text-indigo-600 hover:text-indigo-800"
              >
                Open the AI library
              </Link>
            </CardContent>
          </Card>
        </aside>
      </div>
    </div>
  );
}
