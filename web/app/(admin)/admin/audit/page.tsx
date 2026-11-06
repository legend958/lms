"use client";

import { Fragment, useState, type FormEvent } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { EmptyState, ErrorBanner, PageHeader, Spinner } from "@/components/ui/feedback";
import { Input } from "@/components/ui/input";
import { useAdminAuditLogs, type AdminAuditFilters } from "@/lib/hooks";
import { cn, formatDateTime } from "@/lib/utils";
import type { AuditLogEntry } from "@/lib/types";

interface FilterDraft {
  actorId: string;
  action: string;
  entityType: string;
  entityId: string;
  from: string;
  to: string;
}

const EMPTY_FILTERS: FilterDraft = {
  actorId: "",
  action: "",
  entityType: "",
  entityId: "",
  from: "",
  to: "",
};

function toIso(value: string): string | undefined {
  if (!value) return undefined;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? undefined : parsed.toISOString();
}

function renderValue(value: unknown): string {
  if (value === undefined || value === null) return "—";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

function DiffViewer({ entry }: { entry: AuditLogEntry }) {
  const before = entry.before ?? {};
  const after = entry.after ?? {};
  const keys = Array.from(new Set([...Object.keys(before), ...Object.keys(after)]));

  if (keys.length === 0) {
    return <p className="text-xs text-ink-500">No before/after snapshot was recorded.</p>;
  }

  return (
    <div className="overflow-hidden rounded-lg border border-ink-200">
      <table className="min-w-full divide-y divide-ink-100 text-xs">
        <thead className="bg-ink-50 text-left font-semibold uppercase tracking-wide text-ink-500">
          <tr>
            <th className="px-3 py-2">Field</th>
            <th className="px-3 py-2">Before</th>
            <th className="px-3 py-2">After</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-ink-100">
          {keys.map((key) => {
            const beforeValue = renderValue(before[key]);
            const afterValue = renderValue(after[key]);
            const changed = beforeValue !== afterValue;
            return (
              <tr key={key} className={changed ? "bg-amber-50/40" : undefined}>
                <td className="whitespace-nowrap px-3 py-2 font-mono text-ink-600">{key}</td>
                <td className="px-3 py-2 text-ink-600">
                  <span className="break-all">{beforeValue}</span>
                </td>
                <td
                  className={cn(
                    "px-3 py-2",
                    changed ? "font-medium text-ink-900" : "text-ink-600",
                  )}
                >
                  <span className="break-all">{afterValue}</span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function AdminAuditPage() {
  const [draft, setDraft] = useState<FilterDraft>(EMPTY_FILTERS);
  const [applied, setApplied] = useState<FilterDraft>(EMPTY_FILTERS);
  const [cursorStack, setCursorStack] = useState<Array<string | undefined>>([undefined]);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const cursor = cursorStack[cursorStack.length - 1];
  const filters: AdminAuditFilters = {
    actorId: applied.actorId.trim() || undefined,
    action: applied.action.trim() || undefined,
    entityType: applied.entityType.trim() || undefined,
    entityId: applied.entityId.trim() || undefined,
    from: toIso(applied.from),
    to: toIso(applied.to),
    cursor,
  };
  const auditQuery = useAdminAuditLogs(filters);
  const items = auditQuery.data?.items ?? [];
  const nextCursor = auditQuery.data?.next_cursor ?? null;

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setApplied(draft);
    setCursorStack([undefined]);
    setExpanded(new Set());
  }

  function reset() {
    setDraft(EMPTY_FILTERS);
    setApplied(EMPTY_FILTERS);
    setCursorStack([undefined]);
    setExpanded(new Set());
  }

  function toggleExpanded(id: string) {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <div>
      <PageHeader
        title="Audit log"
        description="Append-only record of every privileged action, with actor, request id, and before/after snapshots."
      />

      <Card className="mb-4">
        <CardContent>
          <form onSubmit={submit} className="grid gap-3 sm:grid-cols-2 lg:grid-cols-6">
            <label className="text-xs font-semibold uppercase tracking-wide text-ink-500">
              Action
              <Input
                className="mt-1"
                value={draft.action}
                onChange={(event) =>
                  setDraft((current) => ({ ...current, action: event.target.value }))
                }
                placeholder="document.delete"
              />
            </label>
            <label className="text-xs font-semibold uppercase tracking-wide text-ink-500">
              Entity type
              <Input
                className="mt-1"
                value={draft.entityType}
                onChange={(event) =>
                  setDraft((current) => ({ ...current, entityType: event.target.value }))
                }
                placeholder="document"
              />
            </label>
            <label className="text-xs font-semibold uppercase tracking-wide text-ink-500">
              Entity id
              <Input
                className="mt-1"
                value={draft.entityId}
                onChange={(event) =>
                  setDraft((current) => ({ ...current, entityId: event.target.value }))
                }
                placeholder="UUID"
              />
            </label>
            <label className="text-xs font-semibold uppercase tracking-wide text-ink-500">
              Actor id
              <Input
                className="mt-1"
                value={draft.actorId}
                onChange={(event) =>
                  setDraft((current) => ({ ...current, actorId: event.target.value }))
                }
                placeholder="UUID"
              />
            </label>
            <label className="text-xs font-semibold uppercase tracking-wide text-ink-500">
              From
              <Input
                className="mt-1"
                type="datetime-local"
                value={draft.from}
                onChange={(event) =>
                  setDraft((current) => ({ ...current, from: event.target.value }))
                }
              />
            </label>
            <label className="text-xs font-semibold uppercase tracking-wide text-ink-500">
              To
              <Input
                className="mt-1"
                type="datetime-local"
                value={draft.to}
                onChange={(event) =>
                  setDraft((current) => ({ ...current, to: event.target.value }))
                }
              />
            </label>
            <div className="flex items-end gap-2 sm:col-span-2 lg:col-span-6">
              <Button type="submit" size="sm">
                Apply filters
              </Button>
              <Button type="button" variant="secondary" size="sm" onClick={reset}>
                Reset
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      {auditQuery.error ? <ErrorBanner message="Could not load the audit log." /> : null}
      {auditQuery.isLoading ? <Spinner label="Loading audit events" /> : null}

      {!auditQuery.isLoading && !auditQuery.error && items.length === 0 ? (
        <EmptyState
          title="No audit events match"
          description="Privileged actions — user changes, imports, permission toggles, and content operations — appear here."
        />
      ) : null}

      {items.length > 0 ? (
        <div className="overflow-hidden rounded-xl border border-ink-200 bg-white shadow-sm">
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-ink-100 text-sm">
              <thead className="bg-ink-50 text-left text-xs font-semibold uppercase tracking-wide text-ink-500">
                <tr>
                  <th className="px-4 py-3">When</th>
                  <th className="px-4 py-3">Actor</th>
                  <th className="px-4 py-3">Action</th>
                  <th className="px-4 py-3">Entity</th>
                  <th className="px-4 py-3">Request</th>
                  <th className="px-4 py-3 text-right">Diff</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-ink-100">
                {items.map((entry) => {
                  const isOpen = expanded.has(entry.id);
                  return (
                    <Fragment key={entry.id}>
                      <tr className="hover:bg-ink-50/60">
                        <td className="whitespace-nowrap px-4 py-3 text-xs text-ink-500">
                          {formatDateTime(entry.created_at)}
                        </td>
                        <td className="px-4 py-3 text-xs text-ink-700">
                          {entry.actor_name ?? "—"}
                          {entry.ip ? (
                            <p className="text-ink-500">{entry.ip}</p>
                          ) : null}
                        </td>
                        <td className="px-4 py-3">
                          <Badge tone="indigo">{entry.action}</Badge>
                        </td>
                        <td className="px-4 py-3 text-xs text-ink-600">
                          <p className="font-medium text-ink-800">{entry.entity_type}</p>
                          {entry.entity_id ? (
                            <p className="font-mono text-[11px] text-ink-500" title={entry.entity_id}>
                              {entry.entity_id}
                            </p>
                          ) : null}
                        </td>
                        <td className="px-4 py-3 text-xs text-ink-500">
                          <span className="font-mono" title={entry.request_id ?? undefined}>
                            {entry.request_id ? `${entry.request_id.slice(0, 8)}…` : "—"}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-right">
                          <Button
                            variant="ghost"
                            size="sm"
                            aria-expanded={isOpen}
                            onClick={() => toggleExpanded(entry.id)}
                          >
                            {isOpen ? "Hide" : "View"}
                          </Button>
                        </td>
                      </tr>
                      {isOpen ? (
                        <tr className="bg-ink-50/40">
                          <td colSpan={6} className="px-4 py-4">
                            <DiffViewer entry={entry} />
                          </td>
                        </tr>
                      ) : null}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      {items.length > 0 ? (
        <div className="mt-4 flex items-center justify-between gap-3">
          <Button
            variant="secondary"
            size="sm"
            disabled={cursorStack.length <= 1 || auditQuery.isFetching}
            onClick={() =>
              setCursorStack((current) => (current.length > 1 ? current.slice(0, -1) : current))
            }
          >
            Previous page
          </Button>
          <span className="text-xs text-ink-500">
            Page {cursorStack.length}
            {auditQuery.isFetching ? " · refreshing…" : ""}
          </span>
          <Button
            variant="secondary"
            size="sm"
            disabled={!nextCursor || auditQuery.isFetching}
            onClick={() => {
              if (nextCursor) {
                setCursorStack((current) => [...current, nextCursor]);
                setExpanded(new Set());
              }
            }}
          >
            Next page
          </Button>
        </div>
      ) : null}
    </div>
  );
}
