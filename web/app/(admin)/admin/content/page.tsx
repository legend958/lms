"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorBanner, PageHeader, Spinner } from "@/components/ui/feedback";
import { Select } from "@/components/ui/input";
import { useToast } from "@/components/ui/toast";
import { ApiError, apiFetch } from "@/lib/api";
import { useAdminContent, useSystemHealth } from "@/lib/hooks";
import { formatDateTime } from "@/lib/utils";
import type {
  AdminContentRow,
  ContentAction,
  ContentDownload,
  Course,
  DocumentStatus,
} from "@/lib/types";

const STATUS_TONE = {
  queued: "neutral",
  extracting: "info",
  chunking: "info",
  embedding: "info",
  indexed: "indigo",
  topic_extraction: "indigo",
  ready: "success",
  failed: "danger",
} as const;

const HEALTH_TONE = {
  ok: "success",
  degraded: "warning",
  fail: "danger",
  disabled: "neutral",
  not_configured: "neutral",
} as const;

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

export default function AdminContentPage() {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [courseId, setCourseId] = useState("");
  const [status, setStatus] = useState<"" | DocumentStatus>("");
  const [actionError, setActionError] = useState<string | null>(null);

  const coursesQuery = useQuery({
    queryKey: ["courses"],
    queryFn: () => apiFetch<Course[]>("/api/v1/courses"),
  });
  const contentQuery = useAdminContent(courseId || undefined, status || undefined);
  const healthQuery = useSystemHealth();

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["admin-content"] });
    queryClient.invalidateQueries({ queryKey: ["admin-audit"] });
  };

  const download = useMutation({
    mutationFn: (documentId: string) =>
      apiFetch<ContentDownload>(`/api/v1/admin/content/${documentId}/download`),
    onSuccess: (payload) => {
      setActionError(null);
      toast({ title: "Download link ready", description: payload.filename, tone: "success" });
      queryClient.invalidateQueries({ queryKey: ["admin-audit"] });
      window.open(payload.url, "_blank", "noopener,noreferrer");
    },
    onError: (err) => {
      const message =
        err instanceof ApiError ? err.message : "Could not create a download link.";
      setActionError(message);
      toast({ title: "Download failed", description: message, tone: "error" });
    },
  });

  const reingest = useMutation({
    mutationFn: (documentId: string) =>
      apiFetch<ContentAction>(`/api/v1/admin/content/${documentId}/reingest`, {
        method: "POST",
      }),
    onSuccess: () => {
      setActionError(null);
      toast({ title: "Re-ingestion queued", tone: "success" });
      refresh();
    },
    onError: (err) => {
      const message =
        err instanceof ApiError ? err.message : "Could not re-ingest the document.";
      setActionError(message);
      toast({ title: "Re-ingest failed", description: message, tone: "error" });
    },
  });

  const remove = useMutation({
    mutationFn: (documentId: string) =>
      apiFetch<void>(`/api/v1/admin/content/${documentId}`, { method: "DELETE" }),
    onSuccess: () => {
      setActionError(null);
      toast({ title: "Document deleted", description: "Rows, vectors, and the stored PDF were removed.", tone: "success" });
      refresh();
    },
    onError: (err) => {
      const message =
        err instanceof ApiError ? err.message : "Could not delete the document.";
      setActionError(message);
      toast({ title: "Delete failed", description: message, tone: "error" });
    },
  });

  const rows = contentQuery.data ?? [];
  const health = healthQuery.data;
  const busy = download.isPending || reingest.isPending || remove.isPending;

  return (
    <div>
      <PageHeader
        title="Content repository"
        description="Every document across every course, with release coverage and destructive controls."
      />

      <Card className="mb-4">
        <CardHeader className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle>System health</CardTitle>
          {health ? (
            <Badge tone={health.status === "ok" ? "success" : "warning"}>
              {health.status}
            </Badge>
          ) : null}
        </CardHeader>
        <CardContent className="space-y-3">
          {healthQuery.isLoading ? <Spinner label="Checking dependencies" /> : null}
          {healthQuery.error ? (
            <p className="text-sm text-ink-500">Health check is unavailable right now.</p>
          ) : null}
          {health ? (
            <>
              <div className="flex flex-wrap gap-2">
                {Object.entries(health.checks).map(([name, value]) => (
                  <span
                    key={name}
                    className="inline-flex items-center gap-1.5 rounded-full border border-ink-200 bg-white px-2.5 py-1 text-xs text-ink-600"
                  >
                    {name}
                    <Badge
                      tone={
                        HEALTH_TONE[value as keyof typeof HEALTH_TONE] ?? "neutral"
                      }
                    >
                      {value}
                    </Badge>
                  </span>
                ))}
                {health.queue_depth !== null ? (
                  <span className="inline-flex items-center gap-1.5 rounded-full border border-ink-200 bg-white px-2.5 py-1 text-xs text-ink-600">
                    queue depth
                    <span className="font-semibold text-ink-800">{health.queue_depth}</span>
                  </span>
                ) : null}
              </div>
              {health.last_ingestion_failure ? (
                <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                  Last ingestion failure: {health.last_ingestion_failure.filename} (
                  {health.last_ingestion_failure.course_code}) —{" "}
                  {health.last_ingestion_failure.error_code ?? "unknown error"}
                </p>
              ) : null}
              <p className="text-xs text-ink-500">
                Groq is a degraded dependency: an outage lowers quality but does not fail
                readiness.
              </p>
            </>
          ) : null}
        </CardContent>
      </Card>

      <Card className="mb-4">
        <CardContent className="flex flex-col gap-3 sm:flex-row sm:items-center">
          <div className="w-full sm:w-72">
            <Select
              value={courseId}
              onChange={(event) => setCourseId(event.target.value)}
              aria-label="Filter by course"
            >
              <option value="">All courses</option>
              {(coursesQuery.data ?? []).map((course) => (
                <option key={course.id} value={course.id}>
                  {course.code} — {course.title}
                </option>
              ))}
            </Select>
          </div>
          <div className="w-full sm:w-52">
            <Select
              value={status}
              onChange={(event) => setStatus(event.target.value as "" | DocumentStatus)}
              aria-label="Filter by status"
            >
              <option value="">All statuses</option>
              {(Object.keys(STATUS_TONE) as DocumentStatus[]).map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </Select>
          </div>
          <div className="text-xs text-ink-500 sm:ml-auto">{rows.length} documents</div>
        </CardContent>
      </Card>

      {actionError ? (
        <div className="mb-4">
          <ErrorBanner message={actionError} />
        </div>
      ) : null}

      {contentQuery.isLoading ? <Spinner label="Loading content" /> : null}
      {contentQuery.error ? <ErrorBanner message="Could not load the content repository." /> : null}

      {!contentQuery.isLoading && !contentQuery.error && rows.length === 0 ? (
        <EmptyState
          title="No documents match"
          description="Adjust the course or status filters. Documents appear here as soon as a teacher uploads them."
        />
      ) : null}

      {rows.length > 0 ? (
        <div className="overflow-hidden rounded-xl border border-ink-200 bg-white shadow-sm">
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-ink-100 text-sm">
              <thead className="bg-ink-50 text-left text-xs font-semibold uppercase tracking-wide text-ink-500">
                <tr>
                  <th className="px-4 py-3">Document</th>
                  <th className="px-4 py-3">Course</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3">Coverage</th>
                  <th className="px-4 py-3">Size</th>
                  <th className="px-4 py-3">Updated</th>
                  <th className="px-4 py-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-ink-100">
                {rows.map((row: AdminContentRow) => {
                  const hasReleased = row.released_topics > 0;
                  return (
                    <tr key={row.id} className="hover:bg-ink-50/60">
                      <td className="max-w-xs px-4 py-3">
                        <p className="truncate font-medium text-ink-900" title={row.filename}>
                          {row.filename}
                        </p>
                        <p className="text-xs text-ink-500">
                          By {row.uploader_name} · {row.page_count ?? "?"} pages
                        </p>
                        {row.error_code ? (
                          <p className="mt-1 text-xs text-rose-600">{row.error_code}</p>
                        ) : null}
                      </td>
                      <td className="px-4 py-3">
                        <p className="font-medium text-ink-800">{row.course_code}</p>
                        <p className="text-xs text-ink-500">{row.course_title}</p>
                      </td>
                      <td className="px-4 py-3">
                        <Badge tone={STATUS_TONE[row.status]}>{row.status}</Badge>
                      </td>
                      <td className="px-4 py-3 text-xs text-ink-600">
                        <p>
                          {row.released_topics}/{row.topic_count} topics released
                        </p>
                        <p className="text-ink-500">{row.chunk_count} chunks</p>
                      </td>
                      <td className="px-4 py-3 text-xs text-ink-500">
                        {formatBytes(row.size_bytes)}
                      </td>
                      <td className="px-4 py-3 text-xs text-ink-500">
                        {formatDateTime(row.updated_at)}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex justify-end gap-1">
                          <Button
                            variant="ghost"
                            size="sm"
                            disabled={busy}
                            onClick={() => download.mutate(row.id)}
                          >
                            Download
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            disabled={busy || hasReleased}
                            title={
                              hasReleased
                                ? "Unrelease this document's topics before re-ingesting"
                                : undefined
                            }
                            onClick={() => {
                              if (window.confirm(`Re-ingest ${row.filename}?`)) {
                                reingest.mutate(row.id);
                              }
                            }}
                          >
                            Re-ingest
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            disabled={busy || hasReleased}
                            className="text-rose-600 hover:bg-rose-50 hover:text-rose-700"
                            title={
                              hasReleased
                                ? "Unrelease this document's topics before deleting"
                                : undefined
                            }
                            onClick={() => {
                              if (
                                window.confirm(
                                  `Permanently delete ${row.filename}?\n\nThis removes the PDF, all chunks and vectors, quizzes, and tasks. The audit snapshot is kept.`,
                                )
                              ) {
                                remove.mutate(row.id);
                              }
                            }}
                          >
                            Delete
                          </Button>
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
    </div>
  );
}
