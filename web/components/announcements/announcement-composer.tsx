"use client";

import { useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input, Select, Textarea } from "@/components/ui/input";
import { ApiError, apiFetch } from "@/lib/api";

export interface CourseOption {
  id: string;
  code: string;
  title: string;
}

function toIso(value: string): string | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

export function AnnouncementComposer({
  courses,
  allowGlobal,
}: {
  courses: CourseOption[];
  allowGlobal: boolean;
}) {
  const queryClient = useQueryClient();
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [courseId, setCourseId] = useState(allowGlobal ? "" : (courses[0]?.id ?? ""));
  const [publishAt, setPublishAt] = useState("");
  const [expiresAt, setExpiresAt] = useState("");
  const [error, setError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: () =>
      apiFetch("/api/v1/announcements", {
        method: "POST",
        body: JSON.stringify({
          title,
          body,
          course_id: courseId || null,
          publish_at: toIso(publishAt),
          expires_at: toIso(expiresAt),
        }),
      }),
    onSuccess: () => {
      setTitle("");
      setBody("");
      setPublishAt("");
      setExpiresAt("");
      setError(null);
      queryClient.invalidateQueries({ queryKey: ["announcements"] });
    },
    onError: (err) => {
      setError(err instanceof ApiError ? err.message : "Could not publish the announcement.");
    },
  });

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    create.mutate();
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>New announcement</CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={onSubmit} className="space-y-4">
          <Field label="Title">
            <Input
              required
              maxLength={200}
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="Week 3 lab update"
            />
          </Field>

          <Field label="Message">
            <Textarea
              required
              value={body}
              onChange={(event) => setBody(event.target.value)}
              placeholder="Share the details students need."
            />
          </Field>

          <Field label="Audience">
            <Select value={courseId} onChange={(event) => setCourseId(event.target.value)}>
              {allowGlobal ? <option value="">Institution-wide</option> : null}
              {courses.map((course) => (
                <option key={course.id} value={course.id}>
                  {course.code} — {course.title}
                </option>
              ))}
            </Select>
          </Field>

          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Publish at" hint="Leave empty to publish now.">
              <Input
                type="datetime-local"
                value={publishAt}
                onChange={(event) => setPublishAt(event.target.value)}
              />
            </Field>
            <Field label="Expires at" hint="Optional.">
              <Input
                type="datetime-local"
                value={expiresAt}
                onChange={(event) => setExpiresAt(event.target.value)}
              />
            </Field>
          </div>

          {error ? (
            <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
              {error}
            </div>
          ) : null}

          <Button
            type="submit"
            className="w-full"
            disabled={create.isPending || (!allowGlobal && courses.length === 0)}
          >
            {create.isPending ? "Publishing…" : "Publish announcement"}
          </Button>
          {courses.length === 0 && !allowGlobal ? (
            <p className="text-xs text-ink-500">
              You are not assigned to a course yet. Ask an administrator for an enrollment.
            </p>
          ) : null}
        </form>
      </CardContent>
    </Card>
  );
}
