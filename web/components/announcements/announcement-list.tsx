"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Badge } from "@/components/ui/badge";
import { EmptyState, ErrorBanner, Spinner } from "@/components/ui/feedback";
import { apiFetch } from "@/lib/api";
import { useMe } from "@/lib/hooks";
import { formatDateTime } from "@/lib/utils";
import type { Announcement } from "@/lib/types";

export function AnnouncementList({
  scheduled = false,
  manage = false,
}: {
  scheduled?: boolean;
  manage?: boolean;
}) {
  const queryClient = useQueryClient();
  const { data: me } = useMe();
  const { data, isLoading, error } = useQuery({
    queryKey: ["announcements", scheduled],
    queryFn: () =>
      apiFetch<Announcement[]>(
        `/api/v1/announcements${scheduled ? "?scheduled=true" : ""}`,
      ),
  });

  const markRead = useMutation({
    mutationFn: (id: string) =>
      apiFetch<void>(`/api/v1/announcements/${id}/read`, { method: "POST" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["announcements"] }),
  });

  const remove = useMutation({
    mutationFn: (id: string) =>
      apiFetch<void>(`/api/v1/announcements/${id}`, { method: "DELETE" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["announcements"] }),
  });

  if (isLoading) return <Spinner label="Loading announcements" />;
  if (error) return <ErrorBanner message="Could not load announcements." />;
  if (!data || data.length === 0) {
    return (
      <EmptyState
        title="No announcements yet"
        description={
          manage
            ? "Publish the first update for your students. Scheduled posts appear here too."
            : "When your teachers post an update it will show up here."
        }
      />
    );
  }

  const now = Date.now();

  return (
    <div className="space-y-3">
      {data.map((announcement) => {
        const future = new Date(announcement.publish_at).getTime() > now;
        const expired = announcement.expires_at
          ? new Date(announcement.expires_at).getTime() <= now
          : false;
        const canDelete =
          manage && me && (me.role === "admin" || me.id === announcement.author_id);

        return (
          <article
            key={announcement.id}
            className={`rounded-xl border bg-white p-5 shadow-sm ${
              announcement.read || manage ? "border-ink-200" : "border-l-4 border-l-indigo-500 border-ink-200"
            }`}
          >
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="text-sm font-semibold text-ink-900">{announcement.title}</h3>
                  {!announcement.read && !manage ? (
                    <Badge tone="indigo">Unread</Badge>
                  ) : null}
                  {future ? <Badge tone="warning">Scheduled</Badge> : null}
                  {expired ? <Badge tone="danger">Expired</Badge> : null}
                </div>
                <p className="mt-1 text-xs text-ink-500">
                  {announcement.course_title ?? "Institution-wide"} ·{" "}
                  {announcement.author_name} · {formatDateTime(announcement.publish_at)}
                  {announcement.expires_at
                    ? ` · expires ${formatDateTime(announcement.expires_at)}`
                    : ""}
                </p>
              </div>

              <div className="flex shrink-0 items-center gap-2">
                {!manage && !announcement.read ? (
                  <button
                    type="button"
                    onClick={() => markRead.mutate(announcement.id)}
                    disabled={markRead.isPending}
                    className="text-xs font-medium text-indigo-600 hover:text-indigo-800 disabled:opacity-50"
                  >
                    Mark read
                  </button>
                ) : null}
                {canDelete ? (
                  <button
                    type="button"
                    onClick={() => {
                      if (window.confirm("Delete this announcement?")) {
                        remove.mutate(announcement.id);
                      }
                    }}
                    disabled={remove.isPending}
                    className="text-xs font-medium text-rose-600 hover:text-rose-800 disabled:opacity-50"
                  >
                    Delete
                  </button>
                ) : null}
              </div>
            </div>
            <p className="mt-3 whitespace-pre-line text-sm leading-relaxed text-ink-700">
              {announcement.body}
            </p>
          </article>
        );
      })}
    </div>
  );
}
