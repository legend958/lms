"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { CsvImportDialog } from "@/components/admin/csv-import-dialog";
import { UserCreateDialog } from "@/components/admin/user-create-dialog";
import { UserEditDialog } from "@/components/admin/user-edit-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { EmptyState, ErrorBanner, PageHeader, Spinner } from "@/components/ui/feedback";
import { Input, Select } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { ApiError, apiFetch } from "@/lib/api";
import { useMe } from "@/lib/hooks";
import { formatDate } from "@/lib/utils";
import type { Course, Page, Role, User } from "@/lib/types";

const ROLE_TONE = {
  student: "info",
  teacher: "indigo",
  admin: "warning",
} as const;

const STATUS_TONE = {
  active: "success",
  inactive: "neutral",
  deleted: "danger",
} as const;

export default function AdminUsersPage() {
  const queryClient = useQueryClient();
  const { data: me } = useMe();
  const [search, setSearch] = useState("");
  const [roleFilter, setRoleFilter] = useState<"" | Role>("");
  const [createOpen, setCreateOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [editing, setEditing] = useState<User | null>(null);
  const [credentials, setCredentials] = useState<{ email: string; password: string } | null>(
    null,
  );
  const [actionError, setActionError] = useState<string | null>(null);

  const usersQuery = useQuery({
    queryKey: ["admin-users", search, roleFilter],
    queryFn: () => {
      const params = new URLSearchParams({ limit: "100" });
      if (search.trim()) params.set("search", search.trim());
      if (roleFilter) params.set("role", roleFilter);
      return apiFetch<Page<User>>(`/api/v1/admin/users?${params.toString()}`);
    },
  });

  const coursesQuery = useQuery({
    queryKey: ["courses"],
    queryFn: () => apiFetch<Course[]>("/api/v1/courses"),
  });

  const remove = useMutation({
    mutationFn: (id: string) =>
      apiFetch<void>(`/api/v1/admin/users/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      setActionError(null);
      queryClient.invalidateQueries({ queryKey: ["admin-users"] });
    },
    onError: (err) => {
      setActionError(err instanceof ApiError ? err.message : "Could not delete the user.");
    },
  });

  const users = usersQuery.data?.items ?? [];

  return (
    <div>
      <PageHeader
        title="Users"
        description="Create accounts, assign roles, and enroll people into courses."
        actions={
          <>
            <Button variant="secondary" onClick={() => setImportOpen(true)}>
              Import CSV
            </Button>
            <Button onClick={() => setCreateOpen(true)}>New user</Button>
          </>
        }
      />

      <Card className="mb-4">
        <CardContent className="flex flex-col gap-3 sm:flex-row sm:items-center">
          <div className="flex-1">
            <Input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search by name or email…"
              aria-label="Search users"
            />
          </div>
          <div className="w-full sm:w-44">
            <Select
              value={roleFilter}
              onChange={(event) => setRoleFilter(event.target.value as "" | Role)}
              aria-label="Filter by role"
            >
              <option value="">All roles</option>
              <option value="student">Students</option>
              <option value="teacher">Teachers</option>
              <option value="admin">Admins</option>
            </Select>
          </div>
          <div className="text-xs text-ink-500 sm:w-24 sm:text-right">
            {users.length} shown
          </div>
        </CardContent>
      </Card>

      {actionError ? (
        <div className="mb-4">
          <ErrorBanner message={actionError} />
        </div>
      ) : null}

      {usersQuery.isLoading ? <Spinner label="Loading users" /> : null}
      {usersQuery.error ? <ErrorBanner message="Could not load users." /> : null}

      {!usersQuery.isLoading && !usersQuery.error && users.length === 0 ? (
        <EmptyState
          title="No users match"
          description="Try a different search or role filter, or create a new account."
          action={<Button onClick={() => setCreateOpen(true)}>New user</Button>}
        />
      ) : null}

      {users.length > 0 ? (
        <div className="overflow-hidden rounded-xl border border-ink-200 bg-white shadow-sm">
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-ink-100 text-sm">
              <thead className="bg-ink-50 text-left text-xs font-semibold uppercase tracking-wide text-ink-500">
                <tr>
                  <th className="px-4 py-3">Person</th>
                  <th className="px-4 py-3">Role</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3">Created</th>
                  <th className="px-4 py-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-ink-100">
                {users.map((user) => (
                  <tr key={user.id} className="hover:bg-ink-50/60">
                    <td className="px-4 py-3">
                      <p className="font-medium text-ink-900">
                        {user.name}
                        {me?.id === user.id ? (
                          <span className="ml-2 text-xs font-normal text-ink-500">you</span>
                        ) : null}
                      </p>
                      <p className="text-xs text-ink-500">{user.email}</p>
                    </td>
                    <td className="px-4 py-3">
                      <Badge tone={ROLE_TONE[user.role]}>{user.role}</Badge>
                    </td>
                    <td className="px-4 py-3">
                      <Badge tone={STATUS_TONE[user.status]}>{user.status}</Badge>
                    </td>
                    <td className="px-4 py-3 text-xs text-ink-500">
                      {formatDate(user.created_at)}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex justify-end gap-1">
                        <Button variant="ghost" size="sm" onClick={() => setEditing(user)}>
                          Edit
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={me?.id === user.id || remove.isPending}
                          className="text-rose-600 hover:bg-rose-50 hover:text-rose-700"
                          onClick={() => {
                            if (window.confirm(`Delete ${user.email}?`)) {
                              remove.mutate(user.id);
                            }
                          }}
                        >
                          Delete
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      <UserCreateDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        courses={coursesQuery.data ?? []}
        onCreated={(created) => setCredentials(created)}
      />

      <CsvImportDialog open={importOpen} onClose={() => setImportOpen(false)} />

      <UserEditDialog user={editing} onClose={() => setEditing(null)} />

      <Modal
        open={Boolean(credentials)}
        onClose={() => setCredentials(null)}
        title="Account created"
        description="Share this temporary password securely. It is shown only once."
        footer={
          <>
            <Button variant="secondary" onClick={() => setCredentials(null)}>
              Close
            </Button>
            <Button
              onClick={() => {
                if (credentials) {
                  void navigator.clipboard?.writeText(
                    `${credentials.email} / ${credentials.password}`,
                  );
                }
              }}
            >
              Copy credentials
            </Button>
          </>
        }
      >
        {credentials ? (
          <div className="space-y-3">
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-ink-500">
                Email
              </p>
              <p className="mt-1 font-mono text-sm text-ink-900">{credentials.email}</p>
            </div>
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-ink-500">
                Temporary password
              </p>
              <p className="mt-1 font-mono text-sm text-ink-900">{credentials.password}</p>
            </div>
            <p className="text-xs text-ink-500">
              The user can change it after signing in (password reset lands in a later phase).
            </p>
          </div>
        ) : null}
      </Modal>
    </div>
  );
}
