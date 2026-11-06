"use client";

import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { CourseEnrollmentDialog } from "@/components/admin/course-enrollment-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { EmptyState, ErrorBanner, PageHeader, Spinner } from "@/components/ui/feedback";
import { Field, Input, Textarea } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { ApiError, apiFetch } from "@/lib/api";
import type { Course } from "@/lib/types";

export default function AdminCoursesPage() {
  const queryClient = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);
  const [managing, setManaging] = useState<Course | null>(null);
  const [code, setCode] = useState("");
  const [title, setTitle] = useState("");
  const [term, setTerm] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const coursesQuery = useQuery({
    queryKey: ["courses"],
    queryFn: () => apiFetch<Course[]>("/api/v1/courses"),
  });

  const create = useMutation({
    mutationFn: () =>
      apiFetch<Course>("/api/v1/courses", {
        method: "POST",
        body: JSON.stringify({
          code,
          title,
          term: term || null,
          description: description || null,
        }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["courses"] });
      setCode("");
      setTitle("");
      setTerm("");
      setDescription("");
      setError(null);
      setCreateOpen(false);
    },
    onError: (err) => {
      setError(err instanceof ApiError ? err.message : "Could not create the course.");
    },
  });

  const archive = useMutation({
    mutationFn: (id: string) =>
      apiFetch<void>(`/api/v1/courses/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      setActionError(null);
      queryClient.invalidateQueries({ queryKey: ["courses"] });
    },
    onError: (err) => {
      setActionError(err instanceof ApiError ? err.message : "Could not archive the course.");
    },
  });

  const restore = useMutation({
    mutationFn: (id: string) =>
      apiFetch<Course>(`/api/v1/courses/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ status: "active" }),
      }),
    onSuccess: () => {
      setActionError(null);
      queryClient.invalidateQueries({ queryKey: ["courses"] });
    },
    onError: (err) => {
      setActionError(err instanceof ApiError ? err.message : "Could not restore the course.");
    },
  });

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    create.mutate();
  }

  const courses = coursesQuery.data ?? [];

  return (
    <div>
      <PageHeader
        title="Courses"
        description="Courses are offerings. Enroll teachers and students to control visibility."
        actions={<Button onClick={() => setCreateOpen(true)}>New course</Button>}
      />

      {actionError ? (
        <div className="mb-4">
          <ErrorBanner message={actionError} />
        </div>
      ) : null}

      {coursesQuery.isLoading ? <Spinner label="Loading courses" /> : null}
      {coursesQuery.error ? <ErrorBanner message="Could not load courses." /> : null}

      {!coursesQuery.isLoading && !coursesQuery.error && courses.length === 0 ? (
        <EmptyState
          title="No courses yet"
          description="Create the first offering, then enroll a teacher and students."
          action={<Button onClick={() => setCreateOpen(true)}>New course</Button>}
        />
      ) : null}

      {courses.length > 0 ? (
        <div className="grid gap-4 sm:grid-cols-2">
          {courses.map((course) => (
            <Card key={course.id}>
              <CardContent className="flex h-full flex-col">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="flex items-center gap-2">
                      <h2 className="text-base font-semibold text-ink-950">{course.title}</h2>
                      <Badge tone="info">{course.code}</Badge>
                    </div>
                    <p className="mt-1 text-xs text-ink-500">
                      {course.term || "No term set"}
                    </p>
                  </div>
                  <Badge tone={course.status === "active" ? "success" : "neutral"}>
                    {course.status}
                  </Badge>
                </div>

                <p className="mt-3 line-clamp-2 flex-1 text-sm text-ink-600">
                  {course.description || "No description yet."}
                </p>

                <div className="mt-4 flex flex-wrap gap-2">
                  <Button size="sm" variant="secondary" onClick={() => setManaging(course)}>
                    Manage enrollments
                  </Button>
                  {course.status === "active" ? (
                    <Button
                      size="sm"
                      variant="ghost"
                      className="text-rose-600 hover:bg-rose-50 hover:text-rose-700"
                      disabled={archive.isPending}
                      onClick={() => {
                        if (window.confirm(`Archive ${course.code}? Students lose access.`)) {
                          archive.mutate(course.id);
                        }
                      }}
                    >
                      Archive
                    </Button>
                  ) : (
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={restore.isPending}
                      onClick={() => restore.mutate(course.id)}
                    >
                      Restore
                    </Button>
                  )}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      ) : null}

      <Modal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        title="Create course"
        description="The code is unique and appears on student dashboards."
        footer={
          <>
            <Button variant="secondary" onClick={() => setCreateOpen(false)}>
              Cancel
            </Button>
            <Button form="create-course-form" type="submit" disabled={create.isPending}>
              {create.isPending ? "Creating…" : "Create course"}
            </Button>
          </>
        }
      >
        <form id="create-course-form" onSubmit={onSubmit} className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-[140px_1fr]">
            <Field label="Code">
              <Input
                required
                minLength={2}
                maxLength={40}
                value={code}
                onChange={(event) => setCode(event.target.value.toUpperCase())}
                placeholder="SCI101"
              />
            </Field>
            <Field label="Title">
              <Input
                required
                maxLength={200}
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                placeholder="Integrated Science"
              />
            </Field>
          </div>
          <Field label="Term" hint="Optional. Example: 2026-T1.">
            <Input
              maxLength={60}
              value={term}
              onChange={(event) => setTerm(event.target.value)}
              placeholder="2026-T1"
            />
          </Field>
          <Field label="Description" hint="Optional. Shown to teachers and admins.">
            <Textarea
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="What this course covers."
            />
          </Field>

          {error ? (
            <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
              {error}
            </div>
          ) : null}
        </form>
      </Modal>

      <CourseEnrollmentDialog course={managing} onClose={() => setManaging(null)} />
    </div>
  );
}
