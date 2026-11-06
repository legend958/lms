"use client";

import { useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { Field, Input, Select } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { ApiError, apiFetch } from "@/lib/api";
import type { Course, Role, User } from "@/lib/types";

type CreatedUser = User & { temporary_password: string | null };

export function UserCreateDialog({
  open,
  onClose,
  courses,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  courses: Course[];
  onCreated: (credentials: { email: string; password: string }) => void;
}) {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("student");
  const [password, setPassword] = useState("");
  const [courseIds, setCourseIds] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: () =>
      apiFetch<CreatedUser>("/api/v1/admin/users", {
        method: "POST",
        body: JSON.stringify({
          name,
          email,
          role,
          course_ids: courseIds,
          password: password.trim() ? password : null,
        }),
      }),
    onSuccess: (user) => {
      queryClient.invalidateQueries({ queryKey: ["admin-users"] });
      if (user.temporary_password) {
        onCreated({ email: user.email, password: user.temporary_password });
      }
      setName("");
      setEmail("");
      setRole("student");
      setPassword("");
      setCourseIds([]);
      setError(null);
      onClose();
    },
    onError: (err) => {
      setError(err instanceof ApiError ? err.message : "Could not create the user.");
    },
  });

  function toggleCourse(id: string) {
    setCourseIds((current) =>
      current.includes(id) ? current.filter((value) => value !== id) : [...current, id],
    );
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    create.mutate();
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Create user"
      description="The user signs in with this email. Leave the password blank to generate a temporary one."
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button form="create-user-form" type="submit" disabled={create.isPending}>
            {create.isPending ? "Creating…" : "Create user"}
          </Button>
        </>
      }
    >
      <form id="create-user-form" onSubmit={onSubmit} className="space-y-4">
        <Field label="Full name">
          <Input
            required
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Asha Patel"
          />
        </Field>
        <Field label="Email">
          <Input
            required
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="asha@school.edu"
          />
        </Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Role">
            <Select
              value={role}
              onChange={(event) => {
                const nextRole = event.target.value as Role;
                setRole(nextRole);
                if (nextRole === "admin") setCourseIds([]);
              }}
            >
              <option value="student">Student</option>
              <option value="teacher">Teacher</option>
              <option value="admin">Admin</option>
            </Select>
          </Field>
          <Field label="Password" hint="Optional, min 8 characters.">
            <Input
              type="password"
              minLength={8}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="Leave blank to generate"
            />
          </Field>
        </div>

        {role !== "admin" ? (
          <div>
            <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-ink-500">
              Course enrollments
            </p>
            <div className="max-h-40 space-y-1 overflow-y-auto rounded-lg border border-ink-200 p-2">
              {courses.length === 0 ? (
                <p className="px-2 py-1 text-xs text-ink-500">No courses yet.</p>
              ) : (
                courses.map((course) => (
                  <label
                    key={course.id}
                    className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-ink-50"
                  >
                    <input
                      type="checkbox"
                      checked={courseIds.includes(course.id)}
                      onChange={() => toggleCourse(course.id)}
                      className="h-4 w-4 rounded border-ink-300 text-indigo-600"
                    />
                    <span className="text-ink-800">
                      {course.code} — {course.title}
                    </span>
                  </label>
                ))
              )}
            </div>
            <p className="mt-1 text-xs text-ink-500">
              Teachers are enrolled as teachers; students as students.
            </p>
          </div>
        ) : null}

        {error ? (
          <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
            {error}
          </div>
        ) : null}
      </form>
    </Modal>
  );
}
