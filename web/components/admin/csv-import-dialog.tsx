"use client";

import { useRef, useState, type DragEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ErrorBanner } from "@/components/ui/feedback";
import { Select } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { useToast } from "@/components/ui/toast";
import { ApiError, apiFetch } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { ImportReport } from "@/lib/types";

const STATUS_TONE = {
  created: "success",
  skipped: "warning",
  error: "danger",
} as const;

export function CsvImportDialog({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [mode, setMode] = useState<"partial" | "atomic">("partial");
  const [dragging, setDragging] = useState(false);
  const [report, setReport] = useState<ImportReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  const upload = useMutation({
    mutationFn: async () => {
      if (!file) throw new Error("Choose a CSV file first");
      const form = new FormData();
      form.set("file", file);
      form.set("mode", mode);
      return apiFetch<ImportReport>("/api/v1/admin/users/import", {
        method: "POST",
        body: form,
      });
    },
    onSuccess: (result) => {
      setReport(result);
      setError(null);
      toast({
        title: `Import finished: ${result.created} created`,
        description: `${result.skipped} skipped · ${result.failed} failed · ${result.total} rows`,
        tone: result.failed > 0 ? "info" : "success",
      });
      queryClient.invalidateQueries({ queryKey: ["admin-users"] });
      queryClient.invalidateQueries({ queryKey: ["admin-audit"] });
    },
    onError: (err) => {
      const message =
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : "Could not import the file.";
      setError(message);
      toast({ title: "Import failed", description: message, tone: "error" });
    },
  });

  function close() {
    if (upload.isPending) return;
    setFile(null);
    setReport(null);
    setError(null);
    setDragging(false);
    onClose();
  }

  function pick(next: File | null) {
    setFile(next);
    setReport(null);
    setError(null);
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    const dropped = event.dataTransfer.files?.[0];
    if (dropped) pick(dropped);
  }

  const created = report?.rows.filter((row) => row.status === "created") ?? [];
  const credentials = created
    .filter((row) => row.email && row.temporary_password)
    .map((row) => `${row.email} / ${row.temporary_password}`)
    .join("\n");

  return (
    <Modal
      open={open}
      onClose={close}
      title="Bulk import users"
      description="CSV columns: name, email, role, and optional course_codes (; or | separated)."
      className="max-w-3xl"
      footer={
        <>
          <Button variant="secondary" onClick={close} disabled={upload.isPending}>
            {report ? "Done" : "Cancel"}
          </Button>
          <Button onClick={() => upload.mutate()} disabled={!file || upload.isPending}>
            {upload.isPending ? "Importing…" : `Import (${mode})`}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-end gap-3">
          <label className="text-sm">
            <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-ink-500">
              Mode
            </span>
            <Select
              value={mode}
              onChange={(event) => setMode(event.target.value as "partial" | "atomic")}
              disabled={Boolean(report) || upload.isPending}
              aria-label="Import mode"
            >
              <option value="partial">Partial — import valid rows</option>
              <option value="atomic">Atomic — all or nothing</option>
            </Select>
          </label>
        </div>

        <div
          role="button"
          tabIndex={0}
          aria-label="Choose or drop a CSV file"
          onClick={() => inputRef.current?.click()}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              inputRef.current?.click();
            }
          }}
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          className={cn(
            "flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed px-6 py-10 text-center transition",
            dragging
              ? "border-indigo-400 bg-indigo-50"
              : "border-ink-200 bg-ink-50/50 hover:border-indigo-300 hover:bg-indigo-50/40",
          )}
        >
          <p className="text-sm font-semibold text-ink-800">
            {file ? file.name : "Drop a CSV here, or click to browse"}
          </p>
          <p className="mt-1 text-xs text-ink-500">
            {file
              ? `${Math.max(1, Math.round(file.size / 1024))} KB · UTF-8, up to 1,000 rows`
              : "UTF-8, up to 2 MB and 1,000 rows"}
          </p>
          <input
            ref={inputRef}
            type="file"
            accept=".csv,text/csv"
            className="sr-only"
            disabled={Boolean(report) || upload.isPending}
            onChange={(event) => pick(event.target.files?.[0] ?? null)}
          />
        </div>

        {error ? <ErrorBanner message={error} /> : null}

        {report ? (
          <div className="space-y-4">
            <div className="flex flex-wrap gap-2 text-sm">
              <Badge tone="neutral">{report.mode}</Badge>
              <Badge tone="success">{report.created} created</Badge>
              <Badge tone="warning">{report.skipped} skipped</Badge>
              <Badge tone="danger">{report.failed} failed</Badge>
              <span className="text-xs text-ink-500">{report.total} rows processed</span>
            </div>

            {credentials ? (
              <div className="rounded-lg border border-indigo-200 bg-indigo-50 p-3">
                <div className="flex items-center justify-between gap-3">
                  <p className="text-xs font-semibold uppercase tracking-wide text-indigo-700">
                    Temporary credentials
                  </p>
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => void navigator.clipboard?.writeText(credentials)}
                  >
                    Copy all
                  </Button>
                </div>
                <pre className="mt-2 whitespace-pre-wrap font-mono text-xs text-indigo-900">
                  {credentials}
                </pre>
              </div>
            ) : null}

            <div className="max-h-72 overflow-auto rounded-lg border border-ink-200">
              <table className="min-w-full divide-y divide-ink-100 text-sm">
                <thead className="sticky top-0 bg-ink-50 text-left text-xs font-semibold uppercase tracking-wide text-ink-500">
                  <tr>
                    <th className="px-3 py-2">Row</th>
                    <th className="px-3 py-2">Email</th>
                    <th className="px-3 py-2">Status</th>
                    <th className="px-3 py-2">Detail</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-ink-100">
                  {report.rows.map((row) => (
                    <tr key={`${row.row_number}-${row.email ?? "blank"}`}>
                      <td className="px-3 py-2 font-mono text-xs text-ink-500">
                        {row.row_number}
                      </td>
                      <td className="px-3 py-2 text-ink-800">{row.email ?? "—"}</td>
                      <td className="px-3 py-2">
                        <Badge tone={STATUS_TONE[row.status]}>{row.status}</Badge>
                      </td>
                      <td className="px-3 py-2 text-xs text-ink-600">
                        {row.temporary_password ? (
                          <span className="font-mono">{row.temporary_password}</span>
                        ) : (
                          (row.reason ?? "—")
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : null}
      </div>
    </Modal>
  );
}
