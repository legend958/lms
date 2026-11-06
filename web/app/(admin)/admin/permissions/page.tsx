"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorBanner, PageHeader, Spinner } from "@/components/ui/feedback";
import { useToast } from "@/components/ui/toast";
import { ApiError, apiFetch } from "@/lib/api";
import { useAdminPermissions } from "@/lib/hooks";
import { cn } from "@/lib/utils";
import type { PermissionRow } from "@/lib/types";

const CAPABILITY_COPY: Record<string, { title: string; description: string }> = {
  "student.self_enroll": {
    title: "Student self-enrollment",
    description:
      "Students can join any active course from the catalog without an admin adding them.",
  },
  "teacher.create_course": {
    title: "Teacher course creation",
    description:
      "Teachers can create new courses. When off, only admins can create courses.",
  },
  "student.assistant_enabled": {
    title: "Study assistant",
    description:
      "Students can ask cited questions against released course material. When off, /student/ask returns 403.",
  },
  "quiz.auto_approve": {
    title: "Quiz auto-approve",
    description:
      "Generated quizzes publish immediately when every question passes validation. When off, teachers review every quiz.",
  },
};

export default function AdminPermissionsPage() {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const permissionsQuery = useAdminPermissions();
  const [actionError, setActionError] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);

  const update = useMutation({
    mutationFn: (row: PermissionRow) =>
      apiFetch<PermissionRow>("/api/v1/admin/permissions", {
        method: "PATCH",
        body: JSON.stringify({ key: row.key, role: row.role, enabled: !row.enabled }),
      }),
    onSuccess: (row) => {
      setActionError(null);
      const copy = CAPABILITY_COPY[row.key];
      toast({
        title: `${copy?.title ?? row.key} ${row.enabled ? "enabled" : "disabled"}`,
        description: "The change is live and recorded in the audit log.",
        tone: "success",
      });
      queryClient.invalidateQueries({ queryKey: ["admin-permissions"] });
      queryClient.invalidateQueries({ queryKey: ["admin-audit"] });
    },
    onError: (err) => {
      const message =
        err instanceof ApiError ? err.message : "Could not update the capability.";
      setActionError(message);
      toast({ title: "Update failed", description: message, tone: "error" });
    },
    onSettled: () => setPending(null),
  });

  function toggle(row: PermissionRow) {
    const next = !row.enabled;
    const verb = next ? "Enable" : "Disable";
    const copy = CAPABILITY_COPY[row.key];
    const warning =
      !next && row.affected_users > 0
        ? `\n\nThis immediately affects ${row.affected_users} active ${row.role} account(s).`
        : "";
    if (window.confirm(`${verb} ${copy?.title ?? row.key}?${warning}`)) {
      setPending(`${row.key}:${row.role}`);
      update.mutate(row);
    }
  }

  const rows = permissionsQuery.data ?? [];

  return (
    <div>
      <PageHeader
        title="Permissions"
        description="Capability flags for the student and teacher portals. Every change is audited."
      />

      {actionError ? (
        <div className="mb-4">
          <ErrorBanner message={actionError} />
        </div>
      ) : null}

      {permissionsQuery.isLoading ? <Spinner label="Loading capabilities" /> : null}
      {permissionsQuery.error ? (
        <ErrorBanner message="Could not load the permission matrix." />
      ) : null}

      {rows.length > 0 ? (
        <div className="overflow-hidden rounded-xl border border-ink-200 bg-white shadow-sm">
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-ink-100 text-sm">
              <thead className="bg-ink-50 text-left text-xs font-semibold uppercase tracking-wide text-ink-500">
                <tr>
                  <th className="px-4 py-3">Capability</th>
                  <th className="px-4 py-3">Role</th>
                  <th className="px-4 py-3">Affects</th>
                  <th className="px-4 py-3 text-right">Enabled</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-ink-100">
                {rows.map((row) => {
                  const copy = CAPABILITY_COPY[row.key];
                  const busy = pending === `${row.key}:${row.role}`;
                  return (
                    <tr key={`${row.key}:${row.role}`} className="hover:bg-ink-50/60">
                      <td className="max-w-xl px-4 py-4">
                        <p className="font-medium text-ink-900">
                          {copy?.title ?? row.key}
                        </p>
                        <p className="mt-1 text-xs leading-relaxed text-ink-500">
                          {copy?.description ?? "Custom capability."}
                        </p>
                        <p className="mt-1 font-mono text-[11px] text-ink-500">{row.key}</p>
                      </td>
                      <td className="px-4 py-4">
                        <Badge tone={row.role === "student" ? "info" : "indigo"}>
                          {row.role}
                        </Badge>
                      </td>
                      <td className="px-4 py-4 text-xs text-ink-600">
                        {row.affected_users} active {row.role}
                        {row.affected_users === 1 ? "" : "s"}
                      </td>
                      <td className="px-4 py-4">
                        <div className="flex items-center justify-end gap-3">
                          <span className="text-xs font-medium text-ink-500">
                            {row.enabled ? "On" : "Off"}
                          </span>
                          <button
                            type="button"
                            role="switch"
                            aria-checked={row.enabled}
                            aria-label={`${row.enabled ? "Disable" : "Enable"} ${copy?.title ?? row.key}`}
                            disabled={busy || update.isPending}
                            onClick={() => toggle(row)}
                            className={cn(
                              "relative h-6 w-11 shrink-0 rounded-full transition focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-300 disabled:opacity-50",
                              row.enabled ? "bg-indigo-600" : "bg-ink-300",
                            )}
                          >
                            <span
                              className={cn(
                                "absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition",
                                row.enabled ? "left-[22px]" : "left-0.5",
                              )}
                            />
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      <Card className="mt-6">
        <CardHeader>
          <CardTitle>Admins always bypass capability gates</CardTitle>
        </CardHeader>
        <CardContent className="text-sm leading-relaxed text-ink-600">
          The matrix governs the student and teacher portals, never the people who
          administer them. Admin actions still write audit rows with actor,
          request id, and before/after snapshots.
        </CardContent>
      </Card>
    </div>
  );
}
