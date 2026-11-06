"use client";

import { useQueries, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
} from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorBanner, Spinner } from "@/components/ui/feedback";
import { Select } from "@/components/ui/input";
import { ApiError, apiFetch, apiUpload } from "@/lib/api";
import { useMe } from "@/lib/hooks";
import type {
  Course,
  DocumentRecord,
  DocumentStatus,
  DocumentStatusInfo,
  DocumentUploadResult,
} from "@/lib/types";
import { cn, formatDateTime } from "@/lib/utils";

const MAX_UPLOAD_MB = 50;
const MAX_FILES_PER_BATCH = 20;
const TERMINAL_STATUSES = new Set<DocumentStatus>(["indexed", "ready", "failed"]);

const STATUS_META = {
  queued: { label: "Queued", tone: "neutral", detail: "Waiting for a worker" },
  extracting: { label: "Extracting", tone: "info", detail: "Reading pages and headings" },
  chunking: { label: "Chunking", tone: "info", detail: "Splitting into study sections" },
  embedding: { label: "Embedding", tone: "indigo", detail: "Building search vectors" },
  indexed: { label: "Indexed", tone: "success", detail: "Searchable by the assistant" },
  topic_extraction: { label: "Grouping topics", tone: "info", detail: "Preparing topics" },
  ready: { label: "Ready", tone: "success", detail: "Released for learning" },
  failed: { label: "Failed", tone: "danger", detail: "Needs attention" },
} as const;

const PROGRESS_FALLBACK: Record<DocumentStatus, number> = {
  queued: 0,
  extracting: 5,
  chunking: 15,
  embedding: 30,
  indexed: 100,
  topic_extraction: 90,
  ready: 100,
  failed: 0,
};

interface UploadEntry {
  id: string;
  file: File;
  progress: number;
  status: "queued" | "uploading" | "done" | "error";
  error?: string;
}

function formatBytes(value: number): string {
  if (value <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  const amount = value / 1024 ** index;
  return `${amount.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

function formatStatus(value: string): string {
  return value.replaceAll("_", " ");
}

function validateFile(file: File): string | null {
  if (!file.name.toLowerCase().endsWith(".pdf")) return "Only PDF files can be uploaded.";
  if (file.size === 0) return "This file is empty.";
  if (file.size > MAX_UPLOAD_MB * 1024 * 1024) {
    return `PDFs must be ${MAX_UPLOAD_MB} MB or smaller.`;
  }
  return null;
}

function newEntryId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function StatusBadge({ status }: { status: DocumentStatus }) {
  const meta = STATUS_META[status];
  return <Badge tone={meta.tone}>{meta.label}</Badge>;
}

function ProgressBar({ percent }: { percent: number }) {
  return (
    <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-ink-100">
      <div
        className="h-full rounded-full bg-indigo-500 transition-[width] duration-500"
        style={{ width: `${Math.max(2, Math.min(100, percent))}%` }}
      />
    </div>
  );
}

export function TeacherDocuments() {
  const queryClient = useQueryClient();
  const { data: me, isLoading: meLoading } = useMe();
  const isAdmin = me?.role === "admin";

  const coursesQuery = useQuery({
    queryKey: ["courses"],
    queryFn: () => apiFetch<Course[]>("/api/v1/courses"),
    enabled: Boolean(me && isAdmin),
  });

  const courseOptions = useMemo(() => {
    if (!me) return [];
    if (isAdmin) {
      return (coursesQuery.data ?? [])
        .filter((course) => course.status === "active")
        .map((course) => ({ id: course.id, code: course.code, title: course.title }));
    }
    return me.courses
      .filter((course) => course.course_role === "teacher")
      .map((course) => ({ id: course.id, code: course.code, title: course.title }));
  }, [coursesQuery.data, isAdmin, me]);

  const [selectedCourseId, setSelectedCourseId] = useState("");
  const [uploads, setUploads] = useState<UploadEntry[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (courseOptions.length === 0) {
      if (selectedCourseId) setSelectedCourseId("");
      return;
    }
    if (!courseOptions.some((course) => course.id === selectedCourseId)) {
      setSelectedCourseId(courseOptions[0].id);
    }
  }, [courseOptions, selectedCourseId]);

  const documentsQuery = useQuery({
    queryKey: ["documents", selectedCourseId],
    queryFn: () =>
      apiFetch<DocumentRecord[]>(
        `/api/v1/documents?course_id=${encodeURIComponent(selectedCourseId)}`,
      ),
    enabled: Boolean(selectedCourseId),
    refetchInterval: (query) => {
      const rows = query.state.data;
      return rows?.some((document) => !TERMINAL_STATUSES.has(document.status)) ? 2000 : false;
    },
  });

  const documents = documentsQuery.data ?? [];
  const activeDocuments = documents.filter((document) => !TERMINAL_STATUSES.has(document.status));
  const statusQueries = useQueries({
    queries: activeDocuments.map((document) => ({
      queryKey: ["document-status", document.id],
      queryFn: () =>
        apiFetch<DocumentStatusInfo>(`/api/v1/documents/${document.id}/status`),
      refetchInterval: 2000,
    })),
  });

  const statusById = useMemo(() => {
    const result = new Map<string, DocumentStatusInfo>();
    statusQueries.forEach((query, index) => {
      const document = activeDocuments[index];
      if (document && query.data) result.set(document.id, query.data);
    });
    return result;
  }, [activeDocuments, statusQueries]);

  const retry = useMutation({
    mutationFn: (documentId: string) =>
      apiFetch<DocumentUploadResult>(`/api/v1/documents/${documentId}/retry`, {
        method: "POST",
      }),
    onSuccess: () => {
      setActionError(null);
      void queryClient.invalidateQueries({ queryKey: ["documents", selectedCourseId] });
    },
    onError: (error) => {
      setActionError(error instanceof ApiError ? error.message : "Could not retry the document.");
    },
  });

  const remove = useMutation({
    mutationFn: (documentId: string) =>
      apiFetch<void>(`/api/v1/documents/${documentId}`, { method: "DELETE" }),
    onSuccess: () => {
      setActionError(null);
      void queryClient.invalidateQueries({ queryKey: ["documents", selectedCourseId] });
    },
    onError: (error) => {
      setActionError(error instanceof ApiError ? error.message : "Could not delete the document.");
    },
  });

  function updateEntry(id: string, patch: Partial<UploadEntry>) {
    setUploads((previous) =>
      previous.map((entry) => (entry.id === id ? { ...entry, ...patch } : entry)),
    );
  }

  async function enqueueFiles(files: FileList | File[]) {
    if (!selectedCourseId) {
      setActionError("Choose a course before uploading.");
      return;
    }
    if (isUploading) {
      setActionError("Wait for the current batch to finish.");
      return;
    }

    const entries: UploadEntry[] = Array.from(files).map((file) => {
      const error = validateFile(file);
      return {
        id: newEntryId(),
        file,
        progress: 0,
        status: error ? "error" : "queued",
        error: error ?? undefined,
      };
    });
    if (entries.length === 0) return;

    let accepted = 0;
    for (const entry of entries) {
      if (entry.status !== "queued") continue;
      if (accepted >= MAX_FILES_PER_BATCH) {
        entry.status = "error";
        entry.error = `A batch can contain at most ${MAX_FILES_PER_BATCH} files.`;
        continue;
      }
      accepted += 1;
    }

    setUploads((previous) => [...previous, ...entries]);
    setActionError(null);

    const pending = entries.filter((entry) => entry.status === "queued");
    if (pending.length === 0) return;

    setIsUploading(true);
    try {
      for (const entry of pending) {
        updateEntry(entry.id, { status: "uploading", progress: 0 });
        const form = new FormData();
        form.append("course_id", selectedCourseId);
        form.append("file", entry.file);

        try {
          await apiUpload<DocumentUploadResult>("/api/v1/documents", form, {
            onProgress: (percent) => updateEntry(entry.id, { progress: percent }),
          });
          updateEntry(entry.id, { status: "done", progress: 100 });
        } catch (error) {
          updateEntry(entry.id, {
            status: "error",
            error: error instanceof ApiError ? error.message : "Upload failed.",
          });
        }
      }
    } finally {
      setIsUploading(false);
      void queryClient.invalidateQueries({ queryKey: ["documents", selectedCourseId] });
    }
  }

  function onFileChange(event: ChangeEvent<HTMLInputElement>) {
    const files = event.target.files;
    if (files && files.length > 0) void enqueueFiles(files);
    event.target.value = "";
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    if (event.dataTransfer.files.length > 0) void enqueueFiles(event.dataTransfer.files);
  }

  function changeCourse(value: string) {
    setSelectedCourseId(value);
    setUploads([]);
    setActionError(null);
  }

  const loading = meLoading || (isAdmin && coursesQuery.isLoading);
  if (loading || !me) {
    return <Spinner label="Loading your courses" />;
  }

  if (isAdmin && coursesQuery.error) {
    return <ErrorBanner message="Could not load courses." />;
  }

  const totalChunks = documents.reduce((sum, document) => sum + document.chunk_count, 0);
  const totalTopics = documents.reduce((sum, document) => sum + document.topic_count, 0);
  const failedCount = documents.filter((document) => document.status === "failed").length;

  return (
    <div>
      <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-ink-950">Documents</h1>
          <p className="mt-1 text-sm text-ink-500">
            Upload the PDFs students should be able to learn from. Ingestion runs in the
            background.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Select
            aria-label="Course"
            className="w-full sm:w-64"
            value={selectedCourseId}
            onChange={(event) => changeCourse(event.target.value)}
            disabled={courseOptions.length === 0}
          >
            {courseOptions.length === 0 ? <option value="">No teaching courses</option> : null}
            {courseOptions.map((course) => (
              <option key={course.id} value={course.id}>
                {course.code} - {course.title}
              </option>
            ))}
          </Select>
          <Button
            onClick={() => fileInputRef.current?.click()}
            disabled={!selectedCourseId || isUploading}
          >
            Upload PDFs
          </Button>
        </div>
      </div>

      {actionError ? (
        <div className="mb-4">
          <ErrorBanner message={actionError} />
        </div>
      ) : null}

      {courseOptions.length === 0 ? (
        <EmptyState
          title="No teaching courses yet"
          description="Ask an administrator to enroll you as a teacher in a course. Then you can upload its PDFs here."
        />
      ) : (
        <>
          <div className="mb-6 grid gap-4 sm:grid-cols-4">
            {[
              { label: "Documents", value: documents.length, hint: "In this course" },
              { label: "Indexed chunks", value: totalChunks, hint: "Searchable sections" },
              { label: "Topics", value: totalTopics, hint: "Review and release" },
              { label: "Failed", value: failedCount, hint: "Need a retry" },
            ].map((stat) => (
              <Card key={stat.label}>
                <CardContent>
                  <p className="text-xs font-semibold uppercase tracking-wide text-ink-500">
                    {stat.label}
                  </p>
                  <p className="mt-2 text-3xl font-semibold text-ink-950">{stat.value}</p>
                  <p className="mt-1 text-xs text-ink-500">{stat.hint}</p>
                </CardContent>
              </Card>
            ))}
          </div>

          <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
            <div className="space-y-4">
              <input
                ref={fileInputRef}
                type="file"
                accept="application/pdf,.pdf"
                multiple
                className="hidden"
                onChange={onFileChange}
              />

              <Card
                className={cn(
                  "border-dashed transition-colors",
                  dragging ? "border-indigo-400 bg-indigo-50/60" : "border-ink-200",
                )}
              >
                <CardContent>
                  <div
                    role="button"
                    tabIndex={0}
                    onClick={() => fileInputRef.current?.click()}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        fileInputRef.current?.click();
                      }
                    }}
                    onDragOver={(event) => {
                      event.preventDefault();
                      setDragging(true);
                    }}
                    onDragLeave={() => setDragging(false)}
                    onDrop={onDrop}
                    className="flex cursor-pointer flex-col items-center justify-center rounded-lg px-4 py-10 text-center focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-500"
                  >
                    <div className="flex h-12 w-12 items-center justify-center rounded-full bg-indigo-50 text-lg font-semibold text-indigo-600">
                      +
                    </div>
                    <p className="mt-3 text-sm font-semibold text-ink-900">
                      Drop PDFs here or click to browse
                    </p>
                    <p className="mt-1 text-xs text-ink-500">
                      Up to {MAX_FILES_PER_BATCH} files per batch, {MAX_UPLOAD_MB} MB each.
                    </p>
                    {isUploading ? (
                      <p className="mt-3 text-xs font-medium text-indigo-600">
                        Uploading your batch...
                      </p>
                    ) : null}
                  </div>
                </CardContent>
              </Card>

              {uploads.length > 0 ? (
                <Card>
                  <CardHeader className="flex items-center justify-between gap-3">
                    <CardTitle>Upload queue</CardTitle>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={isUploading}
                      onClick={() =>
                        setUploads((previous) =>
                          previous.filter(
                            (entry) => entry.status === "queued" || entry.status === "uploading",
                          ),
                        )
                      }
                    >
                      Clear finished
                    </Button>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    {uploads.map((entry) => (
                      <div key={entry.id}>
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <p className="truncate text-sm font-medium text-ink-900">
                              {entry.file.name}
                            </p>
                            <p className="text-xs text-ink-500">
                              {formatBytes(entry.file.size)}
                              {entry.status === "error" && entry.error
                                ? ` - ${entry.error}`
                                : ""}
                            </p>
                          </div>
                          <span
                            className={cn(
                              "shrink-0 text-xs font-medium",
                              entry.status === "error"
                                ? "text-rose-600"
                                : entry.status === "done"
                                  ? "text-emerald-600"
                                  : "text-ink-500",
                            )}
                          >
                            {entry.status === "done"
                              ? "Uploaded"
                              : entry.status === "error"
                                ? "Failed"
                                : entry.status === "uploading"
                                  ? `${entry.progress}%`
                                  : "Queued"}
                          </span>
                        </div>
                        {entry.status === "uploading" ? (
                          <ProgressBar percent={entry.progress} />
                        ) : null}
                      </div>
                    ))}
                  </CardContent>
                </Card>
              ) : null}

              <Card>
                <CardHeader className="flex items-center justify-between gap-3">
                  <CardTitle>Course library</CardTitle>
                  <span className="text-xs text-ink-500">
                    {documents.length} document{documents.length === 1 ? "" : "s"}
                  </span>
                </CardHeader>

                {documentsQuery.isLoading ? (
                  <CardContent>
                    <Spinner label="Loading documents" />
                  </CardContent>
                ) : documentsQuery.error ? (
                  <CardContent>
                    <ErrorBanner message="Could not load documents for this course." />
                  </CardContent>
                ) : documents.length === 0 ? (
                  <CardContent>
                    <EmptyState
                      title="No documents yet"
                      description="Upload the first PDF for this course. You will see extraction, chunking, and embedding progress here."
                    />
                  </CardContent>
                ) : (
                  <div className="divide-y divide-ink-100">
                    {documents.map((document) => {
                      const info = statusById.get(document.id);
                      const progress =
                        info?.progress_pct ?? PROGRESS_FALLBACK[document.status] ?? 0;
                      const stage = info?.stage ?? document.status;
                      const attempts = info?.attempts ?? 0;
                      const terminal = TERMINAL_STATUSES.has(document.status);
                      return (
                        <div
                          key={document.id}
                          className="grid gap-3 px-5 py-4 sm:grid-cols-[minmax(0,1fr)_180px_80px_auto] sm:items-center"
                        >
                          <div className="min-w-0">
                            <div className="flex flex-wrap items-center gap-2">
                              <p className="truncate text-sm font-medium text-ink-900">
                                {document.filename}
                              </p>
                              <Badge tone="neutral">{document.course_code}</Badge>
                            </div>
                            <p className="mt-1 text-xs text-ink-500">
                              {document.page_count ?? "?"} page
                              {document.page_count === 1 ? "" : "s"} -{" "}
                              {formatBytes(document.size_bytes)} - {document.uploader_name}
                              {document.status === "ready"
                                ? ` - ${document.topic_count} topic${document.topic_count === 1 ? "" : "s"}`
                                : ""}
                            </p>
                            {document.error_code || document.error_detail ? (
                              <p className="mt-1 text-xs text-rose-600">
                                {document.error_code}
                                {document.error_detail ? `: ${document.error_detail}` : ""}
                              </p>
                            ) : null}
                          </div>

                          <div>
                            <div className="flex flex-wrap items-center gap-2">
                              <StatusBadge status={document.status} />
                              <span className="text-xs text-ink-500">
                                {terminal
                                  ? STATUS_META[document.status].detail
                                  : `${formatStatus(stage)} - ${progress}%`}
                              </span>
                            </div>
                            {!terminal ? <ProgressBar percent={progress} /> : null}
                            {attempts > 1 ? (
                              <p className="mt-1 text-[11px] text-ink-500">
                                Attempt {attempts} of 3
                              </p>
                            ) : null}
                          </div>

                          <div>
                            <p className="text-sm font-semibold text-ink-900">
                              {document.chunk_count}
                            </p>
                            <p className="text-[11px] uppercase tracking-wide text-ink-500">
                              chunks
                            </p>
                          </div>

                          <div className="flex flex-wrap items-center gap-1 sm:justify-end">
                            <span className="mr-2 hidden text-xs text-ink-500 xl:inline">
                              {formatDateTime(document.created_at)}
                            </span>
                            {document.status === "failed" ? (
                              <Button
                                size="sm"
                                variant="secondary"
                                disabled={retry.isPending}
                                onClick={() => retry.mutate(document.id)}
                              >
                                Retry
                              </Button>
                            ) : null}
                            {document.status === "ready" ? (
                              <Link
                                href={`/teacher/documents/${document.id}/topics`}
                                className="inline-flex h-8 items-center justify-center rounded-lg border border-ink-200 bg-white px-3 text-xs font-medium text-ink-800 shadow-sm transition-colors hover:bg-ink-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink-400"
                              >
                                Review topics
                              </Link>
                            ) : null}
                            <Button
                              size="sm"
                              variant="ghost"
                              className="text-rose-600 hover:bg-rose-50 hover:text-rose-700"
                              disabled={remove.isPending}
                              onClick={() => {
                                if (
                                  window.confirm(
                                    `Delete ${document.filename}? Its indexed chunks will be removed too.`,
                                  )
                                ) {
                                  remove.mutate(document.id);
                                }
                              }}
                            >
                              Delete
                            </Button>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </Card>
            </div>

            <aside className="space-y-4">
              <Card>
                <CardHeader>
                  <CardTitle>Ingestion pipeline</CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                  {[
                    ["1", "Upload", "Validated and stored"],
                    ["2", "Extract", "Page text and headings"],
                    ["3", "Chunk", "Section-aware windows"],
                    ["4", "Embed", "MiniLM vectors in Chroma"],
                    ["5", "Group topics", "Draft topics, hidden from students"],
                    ["6", "Ready", "Review and release to students"],
                  ].map(([step, title, hint]) => (
                    <div key={step} className="flex gap-3">
                      <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-ink-100 text-xs font-semibold text-ink-600">
                        {step}
                      </span>
                      <div>
                        <p className="text-sm font-medium text-ink-900">{title}</p>
                        <p className="text-xs text-ink-500">{hint}</p>
                      </div>
                    </div>
                  ))}
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>What happens next</CardTitle>
                </CardHeader>
                <CardContent className="space-y-2 text-sm text-ink-600">
                  <p>
                    Every chunk starts hidden. Students cannot read a topic until you release
                    it, and release is never automatic.
                  </p>
                  <p>
                    Review the extracted topics, fix titles and page ranges, then release the
                    ones that are ready. Unreleasing hides them again immediately.
                  </p>
                </CardContent>
              </Card>
            </aside>
          </div>
        </>
      )}
    </div>
  );
}
