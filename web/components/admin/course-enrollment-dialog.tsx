"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/feedback";
import { Select } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { ApiError, apiFetch } from "@/lib/api";
import type { Course, Enrollment, Page, User } from "@/lib/types";

type EnrollableRole = "student" | "teacher";

export function CourseEnrollmentDialog({
  course,
  onClose,
}: {
  course: Course | null;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<string[]>([]);
  const [courseRole, setCourseRole] = useState<EnrollableRole>("student");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setSelected([]);
    setCourseRole("student");
    setError(null);
  }, [course?.id]);

  const enrollmentsQuery = useQuery({
    queryKey: ["enrollments", course?.id],
    queryFn: () => apiFetch<Enrollment[]>(`/api/v1/courses/${course?.id}/enrollments`),
    enabled: Boolean(course?.id),
  });

  const candidatesQuery = useQuery({
    queryKey: ["admin-users", "enrollment-picker", courseRole],
    queryFn: () =>
      apiFetch<Page<User>>(`/api/v1/admin/users?role=${courseRole}&limit=100`),
    enabled: Boolean(course?.id),
  });

  const enrollments = enrollmentsQuery.data ?? [];
  const enrolledIds = new Set(enrollments.map((row) => row.user_id));
  const available = (candidatesQuery.data?.items ?? []).filter(
    (candidate) => !enrolledIds.has(candidate.id),
  );

  const add = useMutation({
    mutationFn: () =>
      apiFetch(`/api/v1/courses/${course?.id}/enrollments`, {
        method: "POST",
        body: JSON.stringify({ user_ids: selected, course_role: courseRole }),
      }),
    onSuccess: () => {
      setSelected([]);
      setError(null);
      queryClient.invalidateQueries({ queryKey: ["enrollments", course?.id] });
    },
    onError: (err) => {
      setError(err instanceof ApiError ? err.message : "Could not add enrollments.");
    },
  });

  const remove = useMutation({
    mutationFn: (userId: string) =>
      apiFetch<void>(`/api/v1/courses/${course?.id}/enrollments/${userId}`, {
        method: "DELETE",
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["enrollments", course?.id] });
    },
    onError: (err) => {
      setError(err instanceof ApiError ? err.message : "Could not remove the enrollment.");
    },
  });

  function toggle(id: string) {
    setSelected((current) =>
      current.includes(id) ? current.filter((value) => value !== id) : [...current, id],
    );
  }

  return (
    <Modal
      open={Boolean(course)}
      onClose={onClose}
      title={`Enrollments — ${course?.code ?? ""}`}
      description={course?.title}
      className="max-w-2xl"
      footer={
        <>
          <Select
            value={courseRole}
            onChange={(event) => {
              setCourseRole(event.target.value as EnrollableRole);
              setSelected([]);
            }}
            aria-label="Enrollment role"
            className="w-36"
          >
            <option value="student">Students</option>
            <option value="teacher">Teachers</option>
          </Select>
          <Button variant="secondary" onClick={onClose}>
            Done
          </Button>
          <Button
            onClick={() => add.mutate()}
            disabled={selected.length === 0 || add.isPending}
          >
            {add.isPending ? "Adding…" : "Add selected"}
          </Button>
        </>
      }
    >
      {error ? (
        <div className="mb-4 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
          {error}
        </div>
      ) : null}

      <div className="grid gap-5 sm:grid-cols-2">
        <div>
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-500">
            Current ({enrollments.length})
          </h3>
          {enrollmentsQuery.isLoading ? <Spinner /> : null}
          <div className="space-y-1">
            {enrollments.map((row) => (
              <div
                key={row.user_id}
                className="flex items-center justify-between gap-2 rounded-lg border border-ink-100 px-3 py-2"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-ink-900">{row.name}</p>
                  <p className="truncate text-xs text-ink-500">{row.email}</p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Badge tone={row.course_role === "teacher" ? "indigo" : "info"}>
                    {row.course_role}
                  </Badge>
                  <button
                    type="button"
                    onClick={() => remove.mutate(row.user_id)}
                    className="text-xs text-rose-600 hover:text-rose-800"
                  >
                    Remove
                  </button>
                </div>
              </div>
            ))}
            {!enrollmentsQuery.isLoading && enrollments.length === 0 ? (
              <p className="text-sm text-ink-500">Nobody is enrolled yet.</p>
            ) : null}
          </div>
        </div>

        <div>
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-500">
            Add {courseRole}s
          </h3>
          {candidatesQuery.isLoading ? <Spinner /> : null}
          <div className="max-h-64 space-y-1 overflow-y-auto rounded-lg border border-ink-200 p-2">
            {available.map((candidate) => (
              <label
                key={candidate.id}
                className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-ink-50"
              >
                <input
                  type="checkbox"
                  checked={selected.includes(candidate.id)}
                  onChange={() => toggle(candidate.id)}
                  className="h-4 w-4 rounded border-ink-300 text-indigo-600"
                />
                <span className="min-w-0">
                  <span className="block truncate text-ink-800">{candidate.name}</span>
                  <span className="block truncate text-xs text-ink-500">{candidate.email}</span>
                </span>
              </label>
            ))}
            {!candidatesQuery.isLoading && available.length === 0 ? (
              <p className="px-2 py-1 text-xs text-ink-500">
                All {courseRole}s are enrolled.
              </p>
            ) : null}
          </div>
        </div>
      </div>
    </Modal>
  );
}
