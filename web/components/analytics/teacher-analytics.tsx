"use client";

import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorBanner, PageHeader, Spinner } from "@/components/ui/feedback";
import {
  useAnalyticsKpis,
  useAnalyticsStudent,
  useAnalyticsStudents,
  useAnalyticsTopics,
  useMe,
} from "@/lib/hooks";
import { cn, formatDateTime } from "@/lib/utils";
import type {
  AnalyticsStudentRow,
  AnalyticsStudentSort,
  KnowledgeGap,
  TopicAnalytics,
} from "@/lib/types";

const WEEKS = Array.from({ length: 12 }, (_, index) => index + 1);

function formatPct(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${value.toFixed(1)}%`;
}

function formatDuration(seconds: number | null): string {
  if (seconds === null) return "—";
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return minutes > 0 ? `${minutes}m ${remainder}s` : `${remainder}s`;
}

function optionLabel(index: number | null): string {
  if (index === null || index < 0) return "—";
  return String.fromCharCode(65 + index);
}

function heatTone(value: number | null): string {
  if (value === null) return "bg-ink-50 text-ink-500";
  if (value >= 80) return "bg-emerald-50 text-emerald-700";
  if (value >= 60) return "bg-amber-50 text-amber-700";
  return "bg-rose-50 text-rose-700";
}

function SortHeader({
  label,
  value,
  sort,
  onSort,
  className,
}: {
  label: string;
  value: AnalyticsStudentSort;
  sort: AnalyticsStudentSort;
  onSort: (value: AnalyticsStudentSort) => void;
  className?: string;
}) {
  const active = sort === value;
  return (
    <th className={cn("px-4 py-3 text-left font-semibold", className)}>
      <button
        type="button"
        onClick={() => onSort(value)}
        className={cn(
          "inline-flex items-center gap-1 text-xs font-semibold uppercase tracking-wide transition-colors",
          active ? "text-ink-900" : "text-ink-500 hover:text-ink-800",
        )}
      >
        {label}
        <span aria-hidden>{active ? "↓" : "↕"}</span>
      </button>
    </th>
  );
}

function ProgressBar({ value }: { value: number }) {
  return (
    <div className="h-1.5 w-24 overflow-hidden rounded-full bg-ink-100">
      <div
        className="h-full rounded-full bg-indigo-500"
        style={{ width: `${Math.min(Math.max(value, 0), 100)}%` }}
      />
    </div>
  );
}

function StudentRow({
  student,
  selected,
  onSelect,
}: {
  student: AnalyticsStudentRow;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <tr
      tabIndex={0}
      role="button"
      aria-expanded={selected}
      onClick={onSelect}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onSelect();
        }
      }}
      className={cn(
        "cursor-pointer border-b border-ink-100 transition-colors last:border-0",
        selected ? "bg-indigo-50/70" : "hover:bg-ink-50",
      )}
    >
      <td className="px-4 py-3">
        <p className="font-medium text-ink-900">{student.name}</p>
        <p className="text-xs text-ink-500">{student.email}</p>
      </td>
      <td className="px-4 py-3">
        <div className="flex items-center gap-2">
          <ProgressBar value={student.completion_rate} />
          <span className="text-xs font-medium text-ink-700">
            {formatPct(student.completion_rate)}
          </span>
        </div>
      </td>
      <td className="px-4 py-3 text-xs text-ink-600">
        <span className="font-semibold text-ink-800">{student.completed}</span> done
        <span className="mx-1 text-ink-300">/</span>
        <span className="font-semibold text-ink-800">{student.pending}</span> pending
      </td>
      <td className="px-4 py-3 text-sm font-medium text-ink-800">
        {formatPct(student.avg_score)}
      </td>
      <td className="px-4 py-3 text-sm text-ink-700">{student.days_active}</td>
      <td className="px-4 py-3 text-xs text-ink-500">
        {student.last_active_at ? formatDateTime(student.last_active_at) : "Never"}
      </td>
      <td className="px-4 py-3">
        {student.at_risk ? (
          <Badge tone="danger">At risk</Badge>
        ) : student.weak_topic_count > 0 ? (
          <Badge tone="warning">
            {student.weak_topic_count} weak topic{student.weak_topic_count === 1 ? "" : "s"}
          </Badge>
        ) : (
          <Badge tone="success">On track</Badge>
        )}
      </td>
    </tr>
  );
}

function TopicRow({ topic }: { topic: TopicAnalytics }) {
  const releaseTone =
    topic.release_state === "released"
      ? "success"
      : topic.release_state === "scheduled"
        ? "info"
        : "neutral";
  return (
    <tr className="border-b border-ink-100 last:border-0">
      <td className="px-4 py-3">
        <p className="font-medium text-ink-900">{topic.title}</p>
        <p className="text-xs text-ink-500">{topic.document_title}</p>
      </td>
      <td className="px-4 py-3">
        <Badge tone={releaseTone}>{topic.release_state}</Badge>
      </td>
      <td className="px-4 py-3">
        <div className="flex items-center gap-2">
          <ProgressBar value={topic.completion_rate} />
          <span className="text-xs font-medium text-ink-700">
            {formatPct(topic.completion_rate)}
          </span>
        </div>
        <p className="mt-1 text-[11px] text-ink-500">
          {topic.completed} of {topic.students} students
        </p>
      </td>
      <td className="px-4 py-3 text-sm font-medium text-ink-800">
        {formatPct(topic.avg_score)}
      </td>
      <td className="px-4 py-3">
        <span
          className={cn(
            "inline-flex min-w-[64px] justify-center rounded-md px-2 py-1 text-xs font-semibold",
            heatTone(topic.question_accuracy),
          )}
        >
          {formatPct(topic.question_accuracy)}
        </span>
        <p className="mt-1 text-[11px] text-ink-500">{topic.attempt_count} attempts</p>
      </td>
    </tr>
  );
}

function GapList({ gaps, empty }: { gaps: KnowledgeGap[]; empty: string }) {
  if (gaps.length === 0) {
    return <p className="text-sm text-ink-500">{empty}</p>;
  }
  return (
    <ul className="space-y-2">
      {gaps.map((gap) => (
        <li
          key={`${gap.kind}-${gap.question_id ?? gap.topic_id}`}
          className="flex items-start justify-between gap-3 rounded-lg border border-ink-100 px-3 py-2"
        >
          <div className="min-w-0">
            <p className="truncate text-sm font-medium text-ink-800">
              {gap.question_stem ?? gap.topic_title}
            </p>
            <p className="text-[11px] text-ink-500">
              {gap.kind === "question" ? gap.topic_title : "Topic"} · {gap.answers} answers
            </p>
          </div>
          <span
            className={cn(
              "shrink-0 rounded-md px-2 py-1 text-xs font-semibold",
              heatTone(gap.accuracy),
            )}
          >
            {formatPct(gap.accuracy)}
          </span>
        </li>
      ))}
    </ul>
  );
}

function StudentDrilldown({
  courseId,
  studentId,
  onClose,
}: {
  courseId: string;
  studentId: string;
  onClose: () => void;
}) {
  const { data, isLoading, error } = useAnalyticsStudent(courseId, studentId);

  return (
    <Card className="mb-5">
      <CardHeader className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <CardTitle>{data?.student.name ?? "Student detail"}</CardTitle>
          <p className="mt-1 text-xs text-ink-500">
            {data?.student.email ?? "Loading this student's activity…"}
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded-lg border border-ink-200 px-3 py-1.5 text-xs font-medium text-ink-600 hover:bg-ink-50"
        >
          Close
        </button>
      </CardHeader>

      <CardContent>
        {isLoading ? <Spinner label="Loading student detail" /> : null}
        {error ? <ErrorBanner message="Could not load this student's detail." /> : null}

        {data ? (
          <div className="space-y-6">
            <div className="grid gap-3 sm:grid-cols-3 xl:grid-cols-6">
              {[
                { label: "Completion", value: formatPct(data.summary.completion_rate) },
                {
                  label: "Tasks",
                  value: `${data.summary.completed} / ${data.summary.pending}`,
                  hint: "completed / pending",
                },
                { label: "Avg score", value: formatPct(data.summary.avg_score) },
                { label: "Days active", value: String(data.summary.days_active) },
                {
                  label: "Last active",
                  value: data.summary.last_active_at
                    ? formatDateTime(data.summary.last_active_at)
                    : "Never",
                },
                {
                  label: "Signal",
                  value: data.summary.at_risk ? "At risk" : "On track",
                  hint: `${data.summary.weak_topic_count} weak topics`,
                },
              ].map((item) => (
                <div key={item.label} className="rounded-lg border border-ink-100 px-3 py-2">
                  <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-500">
                    {item.label}
                  </p>
                  <p className="mt-1 text-sm font-semibold text-ink-900">{item.value}</p>
                  {item.hint ? <p className="text-[11px] text-ink-500">{item.hint}</p> : null}
                </div>
              ))}
            </div>

            <div>
              <h3 className="mb-2 text-sm font-semibold text-ink-900">Task history</h3>
              {data.tasks.length === 0 ? (
                <p className="text-sm text-ink-500">No published tasks for this student yet.</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[560px] text-sm">
                    <thead className="border-b border-ink-100 text-xs uppercase tracking-wide text-ink-500">
                      <tr>
                        <th className="px-3 py-2 text-left font-semibold">Task</th>
                        <th className="px-3 py-2 text-left font-semibold">Week</th>
                        <th className="px-3 py-2 text-left font-semibold">Due</th>
                        <th className="px-3 py-2 text-left font-semibold">Status</th>
                        <th className="px-3 py-2 text-left font-semibold">Completed</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.tasks.map((task) => (
                        <tr key={task.task_id} className="border-b border-ink-100 last:border-0">
                          <td className="px-3 py-2 font-medium text-ink-800">{task.title}</td>
                          <td className="px-3 py-2 text-ink-600">{task.week_no}</td>
                          <td className="px-3 py-2 text-xs text-ink-500">
                            {formatDateTime(task.due_at)}
                          </td>
                          <td className="px-3 py-2">
                            <Badge
                              tone={task.status === "completed" ? "success" : "warning"}
                            >
                              {task.status}
                            </Badge>
                          </td>
                          <td className="px-3 py-2 text-xs text-ink-500">
                            {task.completed_at ? formatDateTime(task.completed_at) : "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            <div>
              <h3 className="mb-2 text-sm font-semibold text-ink-900">Quiz attempts</h3>
              {data.attempts.length === 0 ? (
                <p className="text-sm text-ink-500">No attempts submitted yet.</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[640px] text-sm">
                    <thead className="border-b border-ink-100 text-xs uppercase tracking-wide text-ink-500">
                      <tr>
                        <th className="px-3 py-2 text-left font-semibold">Quiz</th>
                        <th className="px-3 py-2 text-left font-semibold">Topic</th>
                        <th className="px-3 py-2 text-left font-semibold">Submitted</th>
                        <th className="px-3 py-2 text-left font-semibold">Score</th>
                        <th className="px-3 py-2 text-left font-semibold">Duration</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.attempts.map((attempt) => (
                        <tr
                          key={attempt.attempt_id}
                          className="border-b border-ink-100 last:border-0"
                        >
                          <td className="px-3 py-2 font-medium text-ink-800">
                            {attempt.quiz_title}
                          </td>
                          <td className="px-3 py-2 text-ink-600">{attempt.topic_title}</td>
                          <td className="px-3 py-2 text-xs text-ink-500">
                            {attempt.submitted_at
                              ? formatDateTime(attempt.submitted_at)
                              : "In progress"}
                          </td>
                          <td className="px-3 py-2 text-ink-800">
                            {attempt.score !== null && attempt.max_score !== null
                              ? `${attempt.score}/${attempt.max_score}`
                              : "—"}
                            <span className="ml-2 text-xs text-ink-500">
                              {formatPct(attempt.percentage)}
                            </span>
                          </td>
                          <td className="px-3 py-2 text-xs text-ink-500">
                            {formatDuration(attempt.duration_seconds)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            <div>
              <h3 className="mb-2 text-sm font-semibold text-ink-900">Weakest questions</h3>
              {data.weakest_questions.length === 0 ? (
                <p className="text-sm text-ink-500">No incorrect answers recorded.</p>
              ) : (
                <ul className="space-y-2">
                  {data.weakest_questions.map((question) => (
                    <li
                      key={question.question_id}
                      className="rounded-lg border border-ink-100 px-3 py-2"
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge tone="info">{question.topic_title}</Badge>
                        <span className="text-[11px] text-ink-500">
                          Chose {optionLabel(question.selected_option)} · correct{" "}
                          {optionLabel(question.correct_option)}
                        </span>
                      </div>
                      <p className="mt-1 text-sm font-medium text-ink-800">{question.stem}</p>
                      <p className="mt-1 text-xs text-ink-600">{question.explanation}</p>
                      {question.source_document ? (
                        <p className="mt-1 text-[11px] text-ink-500">
                          Source: {question.source_document}
                          {question.source_page !== null ? `, p. ${question.source_page}` : ""}
                        </p>
                      ) : null}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

export function TeacherAnalytics() {
  const { data: me } = useMe();
  const courses = useMemo(() => {
    const unique = new Map<string, { id: string; code: string; title: string }>();
    for (const course of me?.courses ?? []) {
      if (course.course_role === "teacher") {
        unique.set(course.id, course);
      }
    }
    return [...unique.values()];
  }, [me]);

  const [selectedCourse, setSelectedCourse] = useState("");
  const [week, setWeek] = useState<number | undefined>(undefined);
  const [sort, setSort] = useState<AnalyticsStudentSort>("completion");
  const [selectedStudent, setSelectedStudent] = useState<string | null>(null);

  const courseId = selectedCourse || courses[0]?.id || "";
  const kpis = useAnalyticsKpis(courseId, week);
  const students = useAnalyticsStudents(courseId, week, sort);
  const topics = useAnalyticsTopics(courseId);

  const studentRows = students.data?.items ?? [];
  const topicRows = topics.data?.items ?? [];
  const gaps = topics.data?.gaps ?? [];
  const topicGaps = gaps.filter((gap) => gap.kind === "topic");
  const questionGaps = gaps.filter((gap) => gap.kind === "question").slice(0, 8);

  function changeCourse(value: string) {
    setSelectedCourse(value);
    setSelectedStudent(null);
  }

  function changeSort(value: AnalyticsStudentSort) {
    setSort(value);
  }

  return (
    <div>
      <PageHeader
        title="Analytics"
        description="Who is behind, on what, and how do I know. Every number is aggregated over the selected course."
        actions={
          courses.length > 0 ? (
            <div className="flex flex-wrap items-center gap-2">
              <label className="flex items-center gap-2 text-xs font-medium text-ink-500">
                Course
                <select
                  value={courseId}
                  onChange={(event) => changeCourse(event.target.value)}
                  className="h-9 rounded-lg border border-ink-200 bg-white px-2 text-xs text-ink-800 shadow-sm focus:border-indigo-400 focus:outline-none"
                >
                  {courses.map((course) => (
                    <option key={course.id} value={course.id}>
                      {course.code} — {course.title}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex items-center gap-2 text-xs font-medium text-ink-500">
                Week
                <select
                  value={week ?? ""}
                  onChange={(event) =>
                    setWeek(event.target.value ? Number(event.target.value) : undefined)
                  }
                  className="h-9 rounded-lg border border-ink-200 bg-white px-2 text-xs text-ink-800 shadow-sm focus:border-indigo-400 focus:outline-none"
                >
                  <option value="">All weeks</option>
                  {WEEKS.map((value) => (
                    <option key={value} value={value}>
                      Week {value}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          ) : undefined
        }
      />

      {courses.length === 0 ? (
        <EmptyState
          title="No courses to analyze"
          description="Analytics appear for courses you teach. Ask an administrator to enroll you as a teacher on a course."
        />
      ) : (
        <>
          <div className="mb-5 grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
            {[
              {
                label: "Students",
                value: kpis.data ? String(kpis.data.students) : "—",
                hint: "Active enrollments",
              },
              {
                label: "Completion",
                value: formatPct(kpis.data?.completion_rate),
                hint: week !== undefined ? `Week ${week}` : "All published tasks",
              },
              {
                label: "Tasks",
                value: kpis.data ? `${kpis.data.completed} / ${kpis.data.pending}` : "—",
                hint: "Completed / pending",
              },
              {
                label: "Avg quiz score",
                value: formatPct(kpis.data?.avg_score),
                hint: "Submitted attempts",
              },
              {
                label: "Active today",
                value: kpis.data ? String(kpis.data.active_today) : "—",
                hint: "Students seen today",
              },
            ].map((stat) => (
              <Card key={stat.label}>
                <CardContent>
                  <p className="text-xs font-semibold uppercase tracking-wide text-ink-500">
                    {stat.label}
                  </p>
                  <p className="mt-2 text-2xl font-semibold text-ink-950">{stat.value}</p>
                  <p className="mt-1 text-xs text-ink-500">{stat.hint}</p>
                </CardContent>
              </Card>
            ))}
          </div>

          {kpis.error || students.error ? (
            <div className="mb-5">
              <ErrorBanner message="Could not load analytics. Try again in a moment." />
            </div>
          ) : null}

          <Card className="mb-5 overflow-hidden">
            <CardHeader className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <CardTitle>Students</CardTitle>
                <p className="mt-1 text-xs text-ink-500">
                  Click a row for task history, attempts, and weakest questions.
                </p>
              </div>
              {students.isLoading ? <Spinner label="Loading students" /> : null}
            </CardHeader>

            {!students.isLoading && studentRows.length === 0 ? (
              <CardContent>
                <EmptyState
                  title="No students enrolled"
                  description="Enroll students in this course to see completion, activity, and score trends."
                />
              </CardContent>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[900px] text-sm">
                  <thead className="border-b border-ink-100 bg-ink-50/60">
                    <tr>
                      <SortHeader label="Student" value="name" sort={sort} onSort={changeSort} />
                      <SortHeader
                        label="Completion"
                        value="completion"
                        sort={sort}
                        onSort={changeSort}
                      />
                      <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-ink-500">
                        Tasks
                      </th>
                      <SortHeader
                        label="Avg score"
                        value="avg_score"
                        sort={sort}
                        onSort={changeSort}
                      />
                      <SortHeader
                        label="Days active"
                        value="days_active"
                        sort={sort}
                        onSort={changeSort}
                      />
                      <SortHeader
                        label="Last active"
                        value="last_active"
                        sort={sort}
                        onSort={changeSort}
                      />
                      <SortHeader label="Signal" value="at_risk" sort={sort} onSort={changeSort} />
                    </tr>
                  </thead>
                  <tbody>
                    {studentRows.map((student) => (
                      <StudentRow
                        key={student.id}
                        student={student}
                        selected={selectedStudent === student.id}
                        onSelect={() =>
                          setSelectedStudent((current) =>
                            current === student.id ? null : student.id,
                          )
                        }
                      />
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          {selectedStudent ? (
            <StudentDrilldown
              courseId={courseId}
              studentId={selectedStudent}
              onClose={() => setSelectedStudent(null)}
            />
          ) : null}

          <Card className="mb-5 overflow-hidden">
            <CardHeader>
              <CardTitle>Topic performance</CardTitle>
              <p className="mt-1 text-xs text-ink-500">
                Question accuracy is the share of submitted answers that were correct.
              </p>
            </CardHeader>

            {topics.isLoading ? (
              <CardContent>
                <Spinner label="Loading topics" />
              </CardContent>
            ) : topicRows.length === 0 ? (
              <CardContent>
                <EmptyState
                  title="No topics yet"
                  description="Upload a document and release a topic to start collecting analytics."
                />
              </CardContent>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[820px] text-sm">
                  <thead className="border-b border-ink-100 bg-ink-50/60">
                    <tr>
                      <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-ink-500">
                        Topic
                      </th>
                      <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-ink-500">
                        Release
                      </th>
                      <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-ink-500">
                        Completion
                      </th>
                      <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-ink-500">
                        Avg score
                      </th>
                      <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-ink-500">
                        Question accuracy
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {topicRows.map((topic) => (
                      <TopicRow key={topic.id} topic={topic} />
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Knowledge gaps</CardTitle>
              <p className="mt-1 text-xs text-ink-500">
                Lowest-accuracy topics and questions across submitted attempts, ranked worst first.
              </p>
            </CardHeader>
            <CardContent className="grid gap-6 lg:grid-cols-2">
              <div>
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-500">
                  Topics
                </h3>
                <GapList gaps={topicGaps} empty="No topic accuracy data yet." />
              </div>
              <div>
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-500">
                  Questions
                </h3>
                <GapList gaps={questionGaps} empty="No question-level answers yet." />
              </div>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
